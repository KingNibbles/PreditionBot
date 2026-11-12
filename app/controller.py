"""Wires everything together; the UI only talks to this class."""
from __future__ import annotations
import logging, os, threading, webbrowser
from pathlib import Path
import cv2
from . import config as C
from .auth import SERVICE
from .detector import Detector
from .dryrun import DryRunTwitch
from .monitor import Monitor
from .obs_client import ObsClient
from .prediction_manager import PredictionManager
from .scheduler import Scheduler
from .state_machine import StateMachine

log = logging.getLogger(__name__)


ENV_KEYS = ("TWITCH_CLIENT_ID", "OBS_PASSWORD")      # the only variables .env is allowed to set


def load_dotenv(path=None):
    path = Path(path) if path else C.APP_DIR / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ENV_KEYS:
            log.warning(".env: ignoring %r (only %s are read)", key, ", ".join(ENV_KEYS))
            continue
        os.environ.setdefault(key, value.strip())


class Controller:
    def __init__(self, on_status=lambda **k: None, on_event=lambda m: None):
        load_dotenv()
        self.cfg = C.load_config()
        self.on_status, self.on_event = on_status, on_event
        self.sched = Scheduler()
        state_path = C.STATE_PATH if not self.cfg.dry_run else C.STATE_PATH.with_name('state_dryrun.json')
        self.sm = StateMachine(state_path, listener=lambda s, r: on_status(state=s.value, reason=r))
        self.manager = None; self.monitor = None; self.auth = None; self.obs = None

    def obs_password(self):
        pw = os.environ.get("OBS_PASSWORD")
        if pw: return pw
        try:
            import keyring; return keyring.get_password(SERVICE, "obs_password") or ""
        except Exception: return ""

    def set_obs_password(self, pw):
        import keyring; keyring.set_password(SERVICE, "obs_password", pw)

    def _auth(self):
        if self.auth is None:
            from .auth import TwitchAuth, KeyringStore
            self.auth = TwitchAuth(self.cfg.twitch_client_id, KeyringStore())
        return self.auth

    def connect_twitch(self, show_code):
        """Device Code flow in a thread. show_code(url, user_code) is called on the worker thread."""
        def work():
            try:
                from .auth import is_twitch_url
                a = self._auth(); flow = a.start_device_flow()
                if not is_twitch_url(flow.get("verification_uri")):
                    raise RuntimeError("Twitch returned an unexpected login address; not opening it.")
                webbrowser.open(flow["verification_uri"]); show_code(flow["verification_uri"], flow["user_code"])
                info = a.poll_device_flow(flow)
                self.on_status(twitch=f"connected as {info.get('login')}")
            except Exception as e:
                log.error("Twitch connect failed: %s", e); self.on_status(twitch=f"error: {e}")
        threading.Thread(target=work, daemon=True).start()

    def disconnect_twitch(self):
        try:
            left = self._auth().revoke_and_disconnect()
        except Exception:
            self.on_status(twitch="disconnect failed; see the log")
            raise
        self.on_status(twitch="disconnected" if not left else
                       "disconnect incomplete: delete the 'valorant-prediction-bot' entries in Windows Credential Manager")

    def twitch_status(self):
        try:
            a = self._auth()
            if not a.is_connected(): return "not connected"
            return f"connected as {a.validate().get('login')}"
        except Exception as e:
            if "different client_id" in str(e):      # token from another app; clear it
                self._auth().disconnect_local()
                return "not connected (Client ID changed: click Connect Twitch)"
            return f"not connected ({e})"

    def build_twitch(self):
        if self.cfg.dry_run:
            return DryRunTwitch()
        if not self.cfg.live_confirmed:
            raise RuntimeError("Live mode needs first-time confirmation (Settings).")
        from .twitch_client import TwitchClient
        a = self._auth(); info = a.validate()
        return TwitchClient(self.cfg.twitch_client_id, info["user_id"], a)

    def _obs(self, password=None, source=None):
        """A new OBS client from the current settings (override the password or source when needed)."""
        c = self.cfg
        return ObsClient(c.obs_host, c.obs_port, self.obs_password() if password is None else password,
                         c.obs_source if source is None else source, c.width, c.height,
                         allow_remote=c.obs_allow_remote)

    def _detector(self):
        """Reload the settings and build a detector from the built-in and user templates."""
        self.cfg = C.load_config()
        specs = C.template_specs(self.cfg)
        if not specs: raise RuntimeError("No templates configured. Use Calibrate first.")
        return Detector(specs, (self.cfg.width, self.cfg.height), self.cfg.default_threshold)

    def start(self):
        if self.monitor and self.monitor.is_alive(): return
        det = self._detector()
        twitch = self.build_twitch()
        self.manager = PredictionManager(self.cfg, self.sm, twitch, self.sched, on_event=self.on_event)
        self.manager.recover()
        self.obs = self._obs()
        self.monitor = Monitor(self.cfg, self.obs, det, self.manager, self.on_status)
        self.monitor.start()
        self.on_status(monitoring=True)

    def stop(self):
        if self.monitor: self.monitor.stop()
        if self.obs: self.obs.close()
        self.on_status(monitoring=False, obs="stopped")
        # an open prediction and its timer are left alone

    def test_detection(self, folder):
        det = self._detector()
        lines = []
        for p in sorted(Path(folder).glob("*")):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg"): continue
            img = cv2.imread(str(p))
            if img is None: continue
            m, top, ms = det.classify(img)
            lines.append(f"{p.name}: {'HIT ' + m.screen if m else 'no hit'} "
                         f"(best {top.screen if top else '-'} {top.score if top else 0:.3f}, {ms:.1f} ms)")
        return lines

    def save_template(self, frame, name, screen, roi, threshold):
        C.check_template_name(name)
        rel = f"templates/{name}.png"
        C.TEMPLATE_DIR.mkdir(exist_ok=True)
        cv2.imwrite(str(C.TEMPLATE_DIR / f"{name}.png"), cv2.resize(frame, (self.cfg.width, self.cfg.height), interpolation=cv2.INTER_AREA))
        self.cfg.templates = [t for t in self.cfg.templates if t["path"] != rel]
        self.cfg.templates.append({"path": rel, "screen": screen, "roi": list(roi), "threshold": threshold})
        C.save_config(self.cfg, C.CONFIG_PATH)

    def grab_frame(self):
        with self._obs() as obs:
            return obs.screenshot()

    def list_obs_sources(self, password=None):
        with self._obs(password, source="") as obs:
            return obs.list_sources()

    def save_obs_source(self, source):
        """Persist the chosen OBS source/scene immediately (config.json, non-secret)."""
        source = (source or "").strip()
        if source == self.cfg.obs_source:
            return False
        self.cfg.obs_source = source
        C.save_config(self.cfg, C.CONFIG_PATH)
        log.info("OBS source saved: %r", source)
        return True

    def save_obs_password(self, pw):
        """Store the OBS password in Windows Credential Manager (never in config.json)."""
        if pw and pw != self.obs_password():
            self.set_obs_password(pw)
            return True
        return False

    def needs_setup(self):
        return not self.cfg.obs_source or not C.effective_templates(self.cfg)

    def setup_progress(self):
        return {"obs": bool(self.cfg.obs_source), "twitch": self.twitch_status().startswith("connected"),
                "templates": {t["screen"] for t in C.effective_templates(self.cfg)}}
