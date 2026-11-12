"""Scan loop: one thread, sleeps between screenshots, reconnects to OBS with bounded backoff."""
from __future__ import annotations
import logging, threading
from .detector import ConfirmationTracker
from .config import START_SCREENS
from .obs_client import ObsError, Backoff

log = logging.getLogger(__name__)


class Monitor(threading.Thread):
    def __init__(self, cfg, obs, detector, manager, on_status=lambda **k: None):
        super().__init__(daemon=True, name="monitor")
        self.cfg, self.obs, self.det, self.mgr, self.on_status = cfg, obs, detector, manager, on_status
        self.stop_evt = threading.Event()
        self.tracker = ConfirmationTracker(cfg.confirmations_required, cfg.detection_cooldown_s)
        self.backoff = Backoff(cfg.obs_backoff_max_s)

    def stop(self): self.stop_evt.set()

    def run(self):
        while not self.stop_evt.is_set():
            if not self.obs.connected:
                try:
                    self.obs.connect(); self.backoff.reset(); self.on_status(obs="connected")
                except ObsError as e:
                    wait = self.backoff.next()
                    self.on_status(obs=f"disconnected (retry {wait:.0f}s)")
                    log.warning("OBS unavailable: %s", e)
                    if self.stop_evt.wait(wait): break
                    continue
            wanted = self.mgr.wanted_screens()
            if not wanted:     # nothing to look for in this state
                self.stop_evt.wait(self.cfg.scan_interval_s); continue
            try:
                frame = self.obs.screenshot()
                match, top, ms = self.det.classify(frame, wanted)
                confirmed = self.tracker.update(match)
                self.mgr.candidate(bool(match and match.screen in START_SCREENS))
                best = match or top
                self.on_status(last=best.screen if best else "-", score=best.score if best else 0.0, ms=ms)
                if confirmed:
                    self.mgr.on_confirmed(confirmed)
            except ObsError as e:
                log.warning("OBS error: %s", e); self.on_status(obs="disconnected")
            except Exception:
                log.exception("scan iteration failed")   # never kill the loop
            self.stop_evt.wait(self.cfg.scan_interval_s)
