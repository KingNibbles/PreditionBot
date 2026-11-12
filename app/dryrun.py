"""In-memory Twitch stand-in. Same interface as TwitchClient; performs NO network I/O."""
from __future__ import annotations
import itertools, logging
from datetime import datetime, timezone, timedelta
from .twitch_client import TwitchTimeout, RateLimited, TwitchError, BadRequest

log = logging.getLogger(__name__)
_ids = itertools.count(1)

class DryRunTwitch:
    def __init__(self, now=lambda: datetime.now(timezone.utc)):
        self.now, self.preds, self.fail_next = now, {}, []   # fail_next: "timeout"|"429"|"500"|"timeout_applied"
    def _maybe_fail(self, what):
        if self.fail_next:
            kind = self.fail_next.pop(0)
            log.warning("[DRY-RUN] simulated failure %s on %s", kind, what)
            if kind == "timeout": raise TwitchTimeout("simulated timeout")
            if kind == "429": raise RateLimited(1)
            if kind == "500": raise TwitchError("simulated 500", 500)
            return kind
        return None
    def _refresh_status(self, p):
        if p["status"] == "ACTIVE":
            end = datetime.fromisoformat(p["created_at"]) + timedelta(seconds=p["prediction_window"])
            if self.now() >= end:
                p["status"] = "LOCKED"; p["locked_at"] = end.isoformat()
    def create_prediction(self, title, outcome_titles, window_s):
        kind = self._maybe_fail("create")
        for p in self.preds.values():
            self._refresh_status(p)
            if p["status"] in ("ACTIVE", "LOCKED"):
                raise BadRequest("simulated: a prediction is already active", 400)
        pid = f"dry-{next(_ids)}"
        p = {"id": pid, "title": title, "status": "ACTIVE", "prediction_window": window_s,
             "created_at": self.now().isoformat(), "winning_outcome_id": None, "locked_at": None,
             "outcomes": [{"id": f"{pid}-o{i}", "title": t, "users": 0, "channel_points": 0}
                          for i, t in enumerate(outcome_titles)]}
        self.preds[pid] = p
        if kind == "timeout_applied":
            raise TwitchTimeout("simulated timeout (request WAS applied)")
        log.info("[DRY-RUN] would CREATE prediction %r window=%ss", title, window_s)
        return dict(p)
    def get_prediction(self, pid):
        self._maybe_fail("get")
        p = self.preds.get(pid)
        if p: self._refresh_status(p)
        return dict(p) if p else None
    def latest_prediction(self):
        self._maybe_fail("latest")
        if not self.preds: return None
        p = list(self.preds.values())[-1]; self._refresh_status(p); return dict(p)
    def end_prediction(self, pid, status, winning_outcome_id=None):
        self._maybe_fail("end")
        p = self.preds.get(pid)
        if not p: raise BadRequest("simulated: not found", 404)
        self._refresh_status(p)
        if p["status"] in ("RESOLVED", "CANCELED"): raise BadRequest("simulated: already ended", 400)
        if status == "RESOLVED" and p["status"] == "ACTIVE":
            raise BadRequest("simulated: lock before resolving", 400)
        p["status"] = status
        if status == "RESOLVED": p["winning_outcome_id"] = winning_outcome_id
        log.info("[DRY-RUN] would PATCH prediction %s -> %s winner=%s", pid, status, winning_outcome_id)
        return dict(p)
