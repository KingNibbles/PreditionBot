"""Explicit state machine with atomic JSON persistence for restart recovery."""
from __future__ import annotations
import json, logging, os, threading
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "IDLE"
    CONFIRMING_START = "CONFIRMING_START"
    CREATING_PREDICTION = "CREATING_PREDICTION"
    PREDICTION_ACTIVE = "PREDICTION_ACTIVE"     # voting window open
    WAITING_FOR_RESULT = "WAITING_FOR_RESULT"   # locked, match in progress
    RESOLVING = "RESOLVING"
    COOLDOWN = "COOLDOWN"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ERROR = "ERROR"


ALLOWED = {
    State.IDLE: {State.CONFIRMING_START, State.PREDICTION_ACTIVE, State.WAITING_FOR_RESULT,
                 State.MANUAL_REVIEW, State.ERROR},
    State.CONFIRMING_START: {State.IDLE, State.CREATING_PREDICTION},
    State.CREATING_PREDICTION: {State.PREDICTION_ACTIVE, State.WAITING_FOR_RESULT, State.IDLE,
                                State.MANUAL_REVIEW, State.ERROR},
    State.PREDICTION_ACTIVE: {State.WAITING_FOR_RESULT, State.RESOLVING, State.MANUAL_REVIEW, State.ERROR},
    State.WAITING_FOR_RESULT: {State.RESOLVING, State.MANUAL_REVIEW, State.ERROR, State.COOLDOWN},
    State.RESOLVING: {State.COOLDOWN, State.MANUAL_REVIEW, State.ERROR, State.WAITING_FOR_RESULT},
    State.COOLDOWN: {State.IDLE},
    State.MANUAL_REVIEW: {State.RESOLVING, State.COOLDOWN, State.IDLE, State.ERROR},
    State.ERROR: {State.IDLE, State.MANUAL_REVIEW, State.COOLDOWN},
}


# everything about the open prediction; cleared when we go back to IDLE
PREDICTION_KEYS = ("prediction_id", "win_id", "lose_id", "created_at", "window_s", "deadline")


class InvalidTransition(Exception):
    pass


class StateMachine:
    """Thread-safe. Persists {state, prediction fields} on every change (atomic replace)."""
    def __init__(self, path: Path, listener=None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = State.IDLE
        self.data = {}
        self.listener = listener
        self.reason = ""

    def load(self):
        if not self.path.exists():
            return False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = State(raw["state"]); self.data = raw.get("data", {})
            return True
        except Exception as e:
            log.error("Corrupt state file (%s); starting IDLE and checking Twitch", e)
            return False

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": self.state.value, "data": self.data}), encoding="utf-8")
        os.replace(tmp, self.path)

    def _notify(self, reason):
        if self.listener:
            try: self.listener(self.state, reason)
            except Exception: log.exception("state listener failed")

    def transition(self, new: State, reason: str = "", **data):
        with self.lock:
            if new == self.state:
                self.data.update(data); self._save(); return
            if new not in ALLOWED[self.state]:
                raise InvalidTransition(f"{self.state.value} -> {new.value} not allowed")
            log.info("STATE %s -> %s %s", self.state.value, new.value, reason)
            self.state, self.reason = new, reason
            self.data.update(data)
            if new == State.IDLE:
                for k in PREDICTION_KEYS:
                    self.data.pop(k, None)
            self._save()
        self._notify(reason)

    def force(self, new: State, reason: str = "", **data):
        """Recovery-only: bypass the transition table (used after reading Twitch's real state)."""
        with self.lock:
            log.warning("FORCE STATE %s -> %s (%s)", self.state.value, new.value, reason)
            self.state, self.reason = new, reason
            self.data.update(data); self._save()
        self._notify(reason)

    def reset(self, reason: str = ""):
        """Back to IDLE with no prediction data (nothing is open on Twitch)."""
        with self.lock:
            self.data.clear()
            self.force(State.IDLE, reason)
