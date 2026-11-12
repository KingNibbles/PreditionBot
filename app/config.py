"""Non-secret settings in config.json. Secrets (tokens, OBS password) never live here."""
from __future__ import annotations
import json, logging, os, re, sys
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

log = logging.getLogger(__name__)

# Public Twitch client id for shared builds. Not a secret. The env var or config.json overrides it.
BUNDLED_CLIENT_ID = ""

_default_home = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
APP_DIR = Path(os.environ.get("VPB_HOME", _default_home))
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"
LOG_DIR = APP_DIR / "logs"
TEMPLATE_DIR = APP_DIR / "templates"

START_SCREENS = ("MATCH_FOUND", "AGENT_SELECT", "MATCH_START")   # any of these opens a prediction
RESULT_SCREENS = ("VICTORY", "DEFEAT", "DRAW")
SCREEN_TYPES = START_SCREENS + RESULT_SCREENS

_TEMPLATE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")


def check_template_name(name):
    """Template names become file names, so only allow plain characters."""
    if not _TEMPLATE_NAME.fullmatch(name or ""):
        raise ValueError("Template name: use only letters, numbers, - and _ (max 40 characters).")
    return name

# Built-in templates, per game and then per resolution. Images live in templates/defaults/<game>/<WxH>/ and the
# boxes are fractions of the frame. To add a game or resolution, drop its images in a new folder and add an entry.
CUSTOM_GAME = "Custom"          # no built-ins: only your own calibrated templates
DEFAULT_GAME = "VALORANT"
DEFAULT_RESOLUTION = "1920x1080"


def _entry(folder, file, screen, roi):
    return {"path": f"templates/defaults/{folder}/{file}", "screen": screen, "roi": roi, "threshold": 0.80}


_VAL = "valorant/1920x1080"
GAMES = {
    "VALORANT": {
        "1920x1080": [
            _entry(_VAL, "match_found.png", "MATCH_FOUND", [0.286, 0.333, 0.429, 0.153]),
            _entry(_VAL, "agent_select.png", "AGENT_SELECT", [0.418, 0.672, 0.157, 0.072]),
            _entry(_VAL, "agent_select.png", "AGENT_SELECT", [0.016, 0.198, 0.208, 0.074]),
            _entry(_VAL, "victory.png", "VICTORY", [0.343, 0.424, 0.315, 0.177]),
            _entry(_VAL, "defeat.png", "DEFEAT", [0.372, 0.424, 0.255, 0.171]),
            _entry(_VAL, "draw.png", "DRAW", [0.390, 0.424, 0.218, 0.171]),
        ],
    },
}
GAME_CHOICES = tuple(GAMES) + (CUSTOM_GAME,)


def resolutions_for(game):
    return tuple(GAMES.get(game, {}))


@dataclass
class TemplateSpec:
    path: str                          # relative to APP_DIR or absolute
    screen: str                        # one of SCREEN_TYPES
    roi: tuple = (0.0, 0.0, 1.0, 1.0)  # x, y, w, h as fractions of the frame
    threshold: float = 0.80

@dataclass
class Config:
    obs_host: str = "localhost"
    obs_port: int = 4455
    obs_allow_remote: bool = False     # off: only an OBS on this PC is accepted
    obs_source: str = ""
    width: int = 640
    height: int = 360
    scan_interval_s: float = 0.5
    confirmations_required: int = 3
    detection_cooldown_s: float = 20.0
    default_threshold: float = 0.80
    game: str = DEFAULT_GAME                    # which game's built-in templates to use
    game_resolution: str = DEFAULT_RESOLUTION   # which resolution of that game
    templates: list = field(default_factory=list)   # your own templates; built-ins are added on top
    prediction_title: str = "Will I win this match?"
    prediction_window_s: int = 60      # Twitch allows 30 to 1800
    result_timeout_min: int = 90
    restart_on_requeue: bool = True    # cancel and restart on a second MATCH FOUND
    requeue_min_age_s: int = 20        # ignore the banner right after creation
    requeue_max_age_s: int = 600       # a new MATCH FOUND inside this window counts as a dodge
    min_match_seconds: int = 300       # results sooner than this go to manual review
    error_clear_s: int = 60            # ERROR goes back to IDLE after this
    ambiguous_policy: str = "manual"   # manual | cancel
    draw_policy: str = "cancel"        # cancel (refund points) | manual
    dry_run: bool = True               # safe default
    live_confirmed: bool = False       # set only after explicit UI confirmation
    twitch_client_id: str = ""         # public client id (not a secret)
    obs_backoff_max_s: float = 30.0

    def validate(self):
        if not (30 <= self.prediction_window_s <= 1800):
            raise ValueError("prediction_window_s must be 30..1800 (Twitch limit)")
        if not (1 <= len(self.prediction_title) <= 45):
            raise ValueError("title must be 1..45 chars (Twitch limit)")
        if not (isinstance(self.obs_port, int) and 1 <= self.obs_port <= 65535):
            raise ValueError("obs_port must be a number from 1 to 65535")
        if self.scan_interval_s < 0.25:
            raise ValueError("scan interval too small")
        if self.ambiguous_policy not in ("manual", "cancel"):
            raise ValueError("bad ambiguous_policy")
        if self.draw_policy not in ("manual", "cancel"):
            raise ValueError("bad draw_policy")
        if self.game not in GAME_CHOICES:
            raise ValueError(f"game must be one of {GAME_CHOICES}")
        if self.game != CUSTOM_GAME and self.game_resolution not in GAMES[self.game]:
            raise ValueError(f"game_resolution for {self.game} must be one of {resolutions_for(self.game)}")

def load_config(path=None) -> Config:
    path = Path(path) if path else CONFIG_PATH
    cfg = Config()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("game_resolution") == "Custom" and "game" not in raw:   # older builds used this for "no built-ins"
            raw["game"], raw["game_resolution"] = CUSTOM_GAME, DEFAULT_RESOLUTION
        known = {f.name for f in fields(Config)}
        for k, v in raw.items():
            if k in known:
                setattr(cfg, k, v)
    # old builds saved the built-ins in config.json; drop those copies
    cfg.templates = [t for t in cfg.templates if not str(t.get("path", "")).replace("\\", "/").startswith("templates/defaults/")]
    if cfg.game not in GAME_CHOICES:
        log.warning("Unknown game %r; using %s", cfg.game, DEFAULT_GAME)
        cfg.game = DEFAULT_GAME
    if cfg.game != CUSTOM_GAME and cfg.game_resolution not in GAMES[cfg.game]:
        log.warning("Unknown resolution %r for %s; using %s", cfg.game_resolution, cfg.game, DEFAULT_RESOLUTION)
        cfg.game_resolution = next(iter(GAMES[cfg.game]))
    cfg.twitch_client_id = os.environ.get("TWITCH_CLIENT_ID") or cfg.twitch_client_id or BUNDLED_CLIENT_ID
    cfg.validate()
    return cfg

def save_config(cfg: Config, path=None):
    path = Path(path) if path else CONFIG_PATH
    cfg.validate()
    d = asdict(cfg)
    # only a client id the user typed in is saved, not the shipped or env one
    if d.get("twitch_client_id") in ("", BUNDLED_CLIENT_ID, os.environ.get("TWITCH_CLIENT_ID")):
        d.pop("twitch_client_id", None)
    tmp = path.with_suffix(".tmp")           # write beside it, then swap, so a crash can't leave half a file
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def effective_templates(cfg: Config):
    """Built-in templates for the chosen game and resolution, plus the user's own."""
    built_in = GAMES.get(cfg.game, {}).get(cfg.game_resolution, [])
    return [dict(t) for t in built_in] + [dict(t) for t in cfg.templates]

def template_specs(cfg: Config):
    root = APP_DIR.resolve()
    specs = []
    for t in effective_templates(cfg):
        path = (APP_DIR / t["path"]).resolve()
        if not path.is_relative_to(root):          # no absolute paths or ".." out of the app folder
            log.warning("Ignoring template outside the app folder: %s", t["path"])
            continue
        specs.append(TemplateSpec(**{**t, "path": str(path), "roi": tuple(t.get("roi", (0, 0, 1, 1)))}))
    return specs
