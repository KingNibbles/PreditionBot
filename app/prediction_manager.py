"""Connects detected screens to the Twitch prediction lifecycle.

Twitch enforces the voting window. We keep its created_at, work out the deadline, and run one timer just
after it that checks the status and only locks if Twitch still says ACTIVE. After a restart the deadline is
rebuilt from Twitch, so the 60 seconds never starts over.
"""
from __future__ import annotations
import logging, math, threading
from datetime import datetime, timezone, timedelta
from .state_machine import State, StateMachine, InvalidTransition
from .config import START_SCREENS, RESULT_SCREENS
from .twitch_client import TwitchError, TwitchTimeout, RateLimited, BadRequest, parse_ts
from .dryrun import DryRunTwitch

log = logging.getLogger(__name__)
OUTCOMES = ("WIN", "LOSE")
LOCK_GRACE_S = 1.5
LOCK_RETRY_S = (5, 15, 30)
COOLDOWN_S = 20
TIMER_KEYS = ("window_end", "lock_retry", "result_timeout")
OPEN_STATES = (State.PREDICTION_ACTIVE, State.WAITING_FOR_RESULT)   # a prediction exists on Twitch


def utcnow(): return datetime.now(timezone.utc)


class PredictionManager:
    def __init__(self, cfg, sm: StateMachine, twitch, scheduler, now=utcnow, on_event=None):
        self.cfg, self.sm, self.twitch, self.sched, self.now = cfg, sm, twitch, scheduler, now
        self.on_event = on_event or (lambda msg: None)
        self.op = threading.RLock()
        self._enforce_dry_run_safety()

    def _enforce_dry_run_safety(self):
        is_dry_client = isinstance(self.twitch, DryRunTwitch)
        if self.cfg.dry_run and not is_dry_client:
            raise RuntimeError("SAFETY: dry_run is on but a live Twitch client was supplied")
        if not self.cfg.dry_run and is_dry_client:
            raise RuntimeError("Dry-run client supplied while dry_run is off")
        if not self.cfg.dry_run and not self.cfg.live_confirmed:
            raise RuntimeError("SAFETY: live mode requires the first-time confirmation")

    def _emit(self, msg):
        tag = "[DRY-RUN] " if self.cfg.dry_run else ""
        log.info("%s%s", tag, msg); self.on_event(tag + msg)

    def wanted_screens(self):
        """Only match templates that matter in the current state (saves CPU)."""
        st = self.sm.state
        if st in (State.IDLE, State.CONFIRMING_START):
            return set(START_SCREENS)
        if st in OPEN_STATES:
            return set(RESULT_SCREENS) | ({"MATCH_FOUND"} if self.cfg.restart_on_requeue else set())
        return set()   # nothing to detect in other states

    def voting_seconds_left(self):
        d = self.sm.data
        if self.sm.state != State.PREDICTION_ACTIVE or "deadline" not in d:
            return 0
        return max(0, int((datetime.fromisoformat(d["deadline"]) - self.now()).total_seconds() + 0.999))

    def snapshot(self):
        d = dict(self.sm.data)
        return {"state": self.sm.state.value, "reason": self.sm.reason, "prediction_id": d.get("prediction_id"),
                "voting_left": self.voting_seconds_left(), "dry_run": self.cfg.dry_run}

    def candidate(self, active: bool):
        """Cheap hint from the scan loop: a (not yet confirmed) match-start candidate is being tracked."""
        with self.op:
            if active and self.sm.state == State.IDLE:
                self.sm.transition(State.CONFIRMING_START, "candidate match-start")
            elif not active and self.sm.state == State.CONFIRMING_START:
                self.sm.transition(State.IDLE, "candidate lost")

    def on_confirmed(self, screen: str):
        with self.op:
            st = self.sm.state
            if screen in START_SCREENS:
                if st in (State.IDLE, State.CONFIRMING_START):
                    self._create()
                elif screen == "MATCH_FOUND" and self.cfg.restart_on_requeue and \
                        st in OPEN_STATES:
                    self._on_requeue()
                else:
                    log.info("%s ignored in state %s (duplicate prevention)", screen, st.value)
            elif screen in RESULT_SCREENS:
                if st in OPEN_STATES:
                    self._on_end_screen(screen)
                else:
                    log.info("%s ignored in state %s", screen, st.value)

    def _age_s(self):
        """Seconds since the open prediction was created."""
        return (self.now() - datetime.fromisoformat(self.sm.data["created_at"])).total_seconds()

    def _on_requeue(self):
        """MATCH FOUND can only show in the menu, so seeing it again while a prediction is open means
        the previous match never started (dodge) and a new queue popped. Cancel + restart."""
        age = self._age_s()
        if age < self.cfg.requeue_min_age_s:
            log.info("MATCH FOUND %ds after creation: same banner, ignored", age); return
        if age >= self.cfg.requeue_max_age_s:
            return self._to_review(f"new MATCH FOUND {int(age)}s after the open prediction was created; "
                                   "the earlier match probably ended unseen. Resolve or cancel it, then re-check")
        self._emit(f"MATCH FOUND again {int(age)}s after creation: assuming a dodge. Canceling and restarting")
        self._cancel("dodge/re-queue")
        if self.sm.state == State.COOLDOWN:
            self.sched.cancel("cooldown")
            self.sm.transition(State.IDLE, "restarting after dodge")
            self._create()

    def _create(self):
        if self.sm.state == State.IDLE:
            self.sm.transition(State.CONFIRMING_START, "confirmed")
        # Twitch allows one open prediction at a time
        try:
            latest = self.twitch.latest_prediction()
        except TwitchError as e:
            return self._fail_before_create(f"cannot verify existing prediction: {e}")
        if latest and latest["status"] in ("ACTIVE", "LOCKED"):
            self._emit(f"Channel already has a {latest['status']} prediction; not creating another")
            self.sm.transition(State.CREATING_PREDICTION, "existing prediction found")
            return self._adopt_or_review(latest, "existing prediction on channel")
        self.sm.transition(State.CREATING_PREDICTION, "creating")
        try:
            p = self.twitch.create_prediction(self.cfg.prediction_title, OUTCOMES, self.cfg.prediction_window_s)
        except TwitchTimeout:
            # unknown if it went through; check Twitch first
            log.warning("create timed out; verifying server state instead of retrying")
            try:
                latest = self.twitch.latest_prediction()
            except TwitchError as e:
                return self._to_review(f"create timed out and state could not be verified: {e}")
            if latest and latest["status"] in ("ACTIVE", "LOCKED") and latest["title"] == self.cfg.prediction_title:
                return self._adopt_or_review(latest, "adopted after timeout")
            return self._fail_before_create("create timed out; Twitch shows no new prediction (not retrying)")
        except RateLimited as e:
            return self._fail_before_create(f"rate limited ({e.retry_after:.0f}s); not retrying this match")
        except TwitchError as e:
            return self._fail_before_create(f"create failed: {e}")
        self._adopt_or_review(p, "created")

    def _fail_before_create(self, why):
        self._emit(why)
        st = self.sm.state
        if st == State.CONFIRMING_START:
            self.sm.transition(State.IDLE, why)
        else:
            self.sm.transition(State.ERROR, why)
            self.sched.schedule("error_clear", self.cfg.error_clear_s, self._clear_error)

    def _clear_error(self):
        with self.op:
            if self.sm.state == State.ERROR and not self.sm.data.get("prediction_id"):
                self.sm.transition(State.IDLE, "error cleared")

    def _to_review(self, why):
        self._emit("MANUAL REVIEW: " + why)
        self.sm.transition(State.MANUAL_REVIEW, why)

    def _adopt_or_review(self, p, how):
        titles = [o["title"] for o in p["outcomes"]]
        if sorted(titles) != sorted(OUTCOMES):
            return self._to_review(f"active prediction has unexpected outcomes {titles}; resolve it on Twitch")
        ids = {o["title"]: o["id"] for o in p["outcomes"]}
        created = parse_ts(p["created_at"])
        window = int(p["prediction_window"])
        skew = (self.now() - created).total_seconds()
        if how == "created" and abs(skew) > 10:   # fresh create but clocks disagree: trust 'now'
            created = self.now()
        deadline = created + timedelta(seconds=window)
        data = dict(prediction_id=p["id"], win_id=ids["WIN"], lose_id=ids["LOSE"],
                    created_at=created.isoformat(), window_s=window, deadline=deadline.isoformat())
        status = p["status"]
        remaining = (deadline - self.now()).total_seconds()
        if status == "ACTIVE" and remaining > 0:
            self.sm.transition(State.PREDICTION_ACTIVE, how, **data)
            self.sched.schedule("window_end", remaining + LOCK_GRACE_S, self._on_window_end)
            self._schedule_result_timeout(created)
            self._emit(f"Prediction {p['id']} {how}; voting open for {math.ceil(remaining)}s")
        elif status in ("ACTIVE", "LOCKED"):
            self.sm.transition(State.WAITING_FOR_RESULT, how, **data)
            if status == "ACTIVE":   # window over but still ACTIVE: lock it
                self._lock_with_retries()
            self._schedule_result_timeout(created)
            self._emit(f"Prediction {p['id']} {how}; locked, waiting for result")
        else:
            self._to_review(f"prediction already {status}")

    def _schedule_result_timeout(self, created):
        left = (created + timedelta(minutes=self.cfg.result_timeout_min) - self.now()).total_seconds()
        self.sched.schedule("result_timeout", max(1, left), self._on_result_timeout)

    def _on_window_end(self):
        with self.op:
            if self.sm.state != State.PREDICTION_ACTIVE:
                return
            self._lock_with_retries()
            if self.sm.state == State.PREDICTION_ACTIVE:
                self.sm.transition(State.WAITING_FOR_RESULT, "voting closed; NOT resolving - match in progress")

    def _lock_with_retries(self, attempt=0):
        """Verify status; PATCH LOCKED only if still ACTIVE. Bounded retries via timers (never a busy loop)."""
        pid = self.sm.data["prediction_id"]
        try:
            p = self.twitch.get_prediction(pid)
            status = p["status"] if p else None
            if status == "ACTIVE":
                self.twitch.end_prediction(pid, "LOCKED")
                self._emit("Voting window over: explicitly LOCKED prediction")
            elif status == "LOCKED":
                self._emit("Twitch auto-locked the prediction at the end of the window")
            elif status in ("RESOLVED", "CANCELED", None):
                self._emit(f"Prediction already {status} on Twitch")
                self._finish("external end")
            return
        except BadRequest as e:
            log.warning("lock check rejected: %s", e)   # e.g. already locked/ended: re-check below
            return
        except TwitchError as e:
            delay = e.retry_after if isinstance(e, RateLimited) else None
            if attempt < len(LOCK_RETRY_S):
                wait = max(delay or 0, LOCK_RETRY_S[attempt])
                log.warning("lock verification failed (%s); retry %d in %ss", e, attempt + 1, wait)
                self.sched.schedule("lock_retry", wait, lambda: self._retry_lock(attempt + 1))
            else:
                self._to_review("could not confirm lock; check the prediction on Twitch")

    def _retry_lock(self, attempt):
        with self.op:
            if self.sm.state in OPEN_STATES:
                self._lock_with_retries(attempt)

    def _on_end_screen(self, screen):
        """A Victory, Defeat or Draw screen was confirmed. Too soon after creation means a remake or a false hit."""
        age = self._age_s()
        if self.sm.state == State.PREDICTION_ACTIVE or age < self.cfg.min_match_seconds:
            return self._ambiguous(f"{screen} screen seen only {int(age)}s after creation (stale or false?)")
        if screen == "DRAW":
            return self._handle_draw()
        self._resolve("WIN" if screen == "VICTORY" else "LOSE", "detected")

    def _handle_draw(self):
        if self.cfg.draw_policy == "cancel":
            self._emit("Draw detected: canceling the prediction so everyone is refunded")
            self._cancel("draw")
        else:
            self._to_review("draw detected; choose Cancel (refund) or a result")

    def _on_result_timeout(self):
        with self.op:
            if self.sm.state in OPEN_STATES:
                self._ambiguous(f"no result after {self.cfg.result_timeout_min} min")

    def _ambiguous(self, why):
        if self.cfg.ambiguous_policy == "cancel":
            self._emit(f"Ambiguous ({why}); policy=cancel")
            self._cancel("ambiguous: " + why)
        else:
            self._to_review(why)

    def _resolve(self, outcome, source):
        pid, wid = self.sm.data["prediction_id"], self.sm.data["win_id" if outcome == "WIN" else "lose_id"]
        self.sm.transition(State.RESOLVING, f"{outcome} ({source})")
        try:
            p = self.twitch.get_prediction(pid)
            if p and p["status"] == "ACTIVE":        # must be locked before choosing a winner
                self.twitch.end_prediction(pid, "LOCKED")
            self.twitch.end_prediction(pid, "RESOLVED", wid)
        except TwitchTimeout:
            return self._verify_resolution(pid, wid, outcome)
        except RateLimited as e:
            return self._to_review(f"rate limited while resolving ({e.retry_after:.0f}s); choose result manually")
        except TwitchError as e:
            return self._verify_resolution(pid, wid, outcome, err=e)
        self._emit(f"Resolved prediction -> {outcome}")
        self._finish("resolved " + outcome)

    def _verify_resolution(self, pid, wid, outcome, err=None):
        try:
            p = self.twitch.get_prediction(pid)
        except TwitchError as e:
            return self._to_review(f"could not verify resolution: {e}")
        if p and p["status"] == "RESOLVED" and p.get("winning_outcome_id") == wid:
            self._emit(f"Resolved prediction -> {outcome} (verified after error)")
            return self._finish("resolved " + outcome)
        self._to_review(f"resolve failed ({err or 'timeout'}); prediction status {p and p['status']}")

    def _cancel(self, why):
        pid = self.sm.data.get("prediction_id")
        try:
            if self.sm.state != State.RESOLVING:
                self.sm.transition(State.RESOLVING, f"cancelling: {why}")
            self.twitch.end_prediction(pid, "CANCELED")
        except TwitchError as e:
            return self._to_review(f"cancel failed: {e}")
        self._emit("Prediction CANCELED; points refunded")
        self._finish("canceled")

    def _finish(self, why):
        for key in TIMER_KEYS:
            self.sched.cancel(key)
        if self.sm.state != State.COOLDOWN:
            try: self.sm.transition(State.COOLDOWN, why)
            except InvalidTransition: self.sm.force(State.COOLDOWN, why)
        self.sched.schedule("cooldown", COOLDOWN_S, self._end_cooldown)

    def _end_cooldown(self):
        with self.op:
            if self.sm.state == State.COOLDOWN:
                self.sm.transition(State.IDLE, "ready for next match")

    def manual_resolve(self, choice):
        """choice: WIN | LOSE | CANCEL  (from MANUAL_REVIEW or while waiting)."""
        with self.op:
            if not self.sm.data.get("prediction_id"):
                raise RuntimeError("no active prediction")
            if choice == "CANCEL":
                return self._cancel("manual")
            if choice not in OUTCOMES:
                raise ValueError(choice)
            self._resolve(choice, "manual")

    def recheck_twitch(self):
        with self.op:
            return self.recover()

    def acknowledge(self):
        """Clear ERROR/MANUAL_REVIEW when no prediction is open on Twitch."""
        with self.op:
            if self.sm.state in (State.ERROR, State.MANUAL_REVIEW):
                self.sm.transition(State.IDLE, "acknowledged by user")

    def recover(self):
        """On startup / re-check: trust Twitch, not the local clock or the saved timer."""
        with self.op:
            self.sm.load()
            pid = self.sm.data.get("prediction_id")
            try:
                p = self.twitch.get_prediction(pid) if pid else self.twitch.latest_prediction()
            except TwitchError as e:
                if pid:
                    return self._to_review(f"cannot reach Twitch to verify prediction {pid}: {e}")
                self.sm.force(State.IDLE, "startup (Twitch unreachable)"); return
            if not p or p["status"] in ("RESOLVED", "CANCELED"):
                return self.sm.reset("recovered: nothing open on Twitch")
            self.sched.cancel_all()
            if p["id"] != pid:
                self._emit("Found an open prediction not recorded locally; adopting if it matches")
            self.sm.force(State.CREATING_PREDICTION, "recovering")  # staging state, replaced below
            self._adopt_or_review(p, "recovered from Twitch")
