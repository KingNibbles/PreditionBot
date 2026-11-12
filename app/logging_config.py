"""Rotating file log plus console. Everything written is scrubbed of tokens, passwords and Windows user names."""
import logging, re, sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_KEYS = "access_token|refresh_token|id_token|client_secret|device_code|password|passwd|secret"
_KEY_VALUE = re.compile(r"(?P<key>(?:" + _SECRET_KEYS + r")[\"']?\s*[:=]\s*[\"']?)(?P<value>[^\s\"',}&)\]]+)", re.I)
_AUTH_HEADER = re.compile(r"(?P<key>\b(?:Bearer|OAuth)\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})", re.I)
_USER_DIR = re.compile(r"(?i)(users[\\/])[^\\/\s\"']+")


def redact(text):
    text = _KEY_VALUE.sub(r"\g<key>***", text)
    text = _AUTH_HEADER.sub(r"\g<key>***", text)
    return _USER_DIR.sub(r"\1<user>", text)


class RedactingFormatter(logging.Formatter):
    """Scrubs the final text, so exception tracebacks are covered as well as the message."""
    def format(self, record):
        return redact(super().format(record))


def setup_logging(log_dir: Path, level=logging.INFO):
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    if root.handlers:
        return log_dir / "app.log"
    handlers = [RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:          # the windowed exe has no console
        handlers.append(logging.StreamHandler())
    fmt = RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_dir / "app.log"
