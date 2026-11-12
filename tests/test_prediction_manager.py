import tempfile, unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app.config import Config
from app.dryrun import DryRunTwitch
from app.prediction_manager import PredictionManager
from tests.fakes import ManualScheduler
from app.state_machine import StateMachine, State

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

class Env:
    def __init__(self, **cfgkw):
        self.t = T0
        self.cfg = Config(dry_run=True, **cfgkw)
        self.tw = DryRunTwitch(now=lambda: self.t)
        self.sched = ManualScheduler()
        self.path = Path(tempfile.mkdtemp()) / "state.json"
        self.sm = StateMachine(self.path)
        self.events = []
        self.mgr = PredictionManager(self.cfg, self.sm, self.tw, self.sched, now=lambda: self.t, on_event=self.events.append)
    def advance(self, s):
        self.t += timedelta(seconds=s)
        return self
    def start(self, screen="MATCH_FOUND"):
        self.mgr.on_confirmed(screen)
        return self
    def lock(self):
        """Let the 60 s voting window pass and fire the lock timer."""
        self.advance(62); self.sched.fire("window_end")
        return self
    def new_manager(self):
        sm = StateMachine(self.path); sm.load()
        self.sm = sm
        self.mgr = PredictionManager(self.cfg, sm, self.tw, self.sched, now=lambda: self.t, on_event=self.events.append)
        return self.mgr

class ManagerTests(unittest.TestCase):
    def test_create_uses_60s_window_and_two_outcomes(self):
        e = Env().start("MATCH_START")
        p = list(e.tw.preds.values())[0]
        self.assertEqual(p["prediction_window"], 60)
        self.assertEqual([o["title"] for o in p["outcomes"]], ["WIN", "LOSE"])
        self.assertEqual(p["title"], "Will I win this match?")
        self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)
        self.assertIn("window_end", e.sched.pending())
        self.assertEqual(e.mgr.voting_seconds_left(), 60)

    def test_countdown_runs_from_creation(self):
        e = Env().start("MATCH_START"); e.advance(25)
        self.assertEqual(e.mgr.voting_seconds_left(), 35)

    def test_duplicate_match_start_creates_one(self):
        e = Env().start("MATCH_START"); e.mgr.on_confirmed("MATCH_START")
        self.assertEqual(len(e.tw.preds), 1)
        self.assertEqual(len(e.sched.pending()), len(set(e.sched.pending())))

    def test_window_end_locks_but_never_resolves(self):
        e = Env().start("MATCH_START"); e.advance(62)
        e.sched.fire("window_end")
        p = list(e.tw.preds.values())[0]
        self.assertEqual(p["status"], "LOCKED"); self.assertIsNone(p["winning_outcome_id"])
        self.assertEqual(e.sm.state, State.WAITING_FOR_RESULT)

    def test_explicit_lock_if_still_active(self):
        e = Env().start("MATCH_START")
        e.advance(62)
        p = list(e.tw.preds.values())[0]
        e.tw._refresh_status = lambda p: None     # server (simulated) failed to auto-lock
        e.sched.fire("window_end")
        self.assertEqual(p["status"], "LOCKED")

    def _locked(self, **kw):
        return Env(**kw).start("MATCH_START").lock()

    def test_victory_resolves_win(self):
        e = self._locked(); e.advance(900); e.mgr.on_confirmed("VICTORY")
        p = list(e.tw.preds.values())[0]
        self.assertEqual(p["status"], "RESOLVED"); self.assertEqual(p["winning_outcome_id"], p["outcomes"][0]["id"])
        self.assertEqual(e.sm.state, State.COOLDOWN)
        e.sched.fire("cooldown"); self.assertEqual(e.sm.state, State.IDLE)

    def test_defeat_resolves_lose(self):
        e = self._locked(); e.advance(900); e.mgr.on_confirmed("DEFEAT")
        p = list(e.tw.preds.values())[0]
        self.assertEqual(p["winning_outcome_id"], p["outcomes"][1]["id"])

    def test_result_during_voting_is_ambiguous(self):
        e = Env().start("MATCH_START"); e.advance(10); e.mgr.on_confirmed("VICTORY")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)
        self.assertIsNone(list(e.tw.preds.values())[0]["winning_outcome_id"])

    def test_too_early_result_ambiguous_cancel_policy(self):
        e = self._locked(ambiguous_policy="cancel"); e.advance(100); e.mgr.on_confirmed("DEFEAT")
        self.assertEqual(list(e.tw.preds.values())[0]["status"], "CANCELED")

    def test_result_timeout_goes_manual(self):
        e = self._locked(); e.sched.fire("result_timeout")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)

    def test_manual_resolution(self):
        e = self._locked(); e.sched.fire("result_timeout"); e.mgr.manual_resolve("LOSE")
        self.assertEqual(list(e.tw.preds.values())[0]["status"], "RESOLVED")

    def test_no_create_while_one_is_open(self):
        e = Env(); e.tw.create_prediction("other", ["A", "B"], 60)
        e.mgr.on_confirmed("MATCH_START")
        self.assertEqual(len(e.tw.preds), 1); self.assertEqual(e.sm.state, State.MANUAL_REVIEW)

    def test_create_timeout_adopted(self):
        e = Env(); e.tw.fail_next = ["timeout_applied"]; e.mgr.on_confirmed("MATCH_START")
        self.assertEqual(len(e.tw.preds), 1); self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)

    def test_create_timeout_not_applied_no_blind_retry(self):
        e = Env(); e.tw.fail_next = ["timeout"]; e.mgr.on_confirmed("MATCH_START")
        # first failure hits latest_prediction() pre-check -> back to IDLE, nothing created
        self.assertEqual(len(e.tw.preds), 0); self.assertEqual(e.sm.state, State.IDLE)

    def test_create_500_clears_without_retry(self):
        e = Env(); e.tw.fail_next = [None, "500"]
        e.tw.fail_next = []
        orig = e.tw.create_prediction
        def boom(*a): raise __import__("app.twitch_client", fromlist=["x"]).TwitchError("500", 500)
        e.tw.create_prediction = boom
        e.mgr.on_confirmed("MATCH_START")
        self.assertEqual(e.sm.state, State.ERROR)
        e.sched.fire("error_clear"); self.assertEqual(e.sm.state, State.IDLE)

    def test_lock_failures_bounded(self):
        e = Env().start("MATCH_START"); e.advance(62)
        e.tw.fail_next = ["500"]
        e.sched.fire("window_end")
        self.assertEqual(e.sm.state, State.WAITING_FOR_RESULT)
        self.assertEqual(e.mgr.voting_seconds_left(), 0)
        self.assertIn("lock_retry", e.sched.pending())
        e.tw.fail_next = ["500"]*3
        for _ in range(4):
            if "lock_retry" in e.sched.pending(): e.sched.fire("lock_retry")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)

    def test_restart_mid_voting_does_not_restart_timer(self):
        e = Env().start("MATCH_START"); e.advance(40)
        e.sched.cancel_all()
        m = e.new_manager(); m.recover()
        self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)
        self.assertEqual(m.voting_seconds_left(), 20)
        delay, _ = e.sched.timers["window_end"]; self.assertAlmostEqual(delay, 21.5, places=1)

    def test_restart_after_lock_waits_for_result(self):
        e = self._locked(); e.sched.cancel_all(); m = e.new_manager(); m.recover()
        self.assertEqual(e.sm.state, State.WAITING_FOR_RESULT)
        self.assertEqual(len(e.tw.preds), 1)

    def test_restart_with_ended_prediction_goes_idle(self):
        e = self._locked(); e.advance(900); e.mgr.on_confirmed("VICTORY")
        m = e.new_manager(); m.recover(); self.assertEqual(e.sm.state, State.IDLE)

    def test_restart_unreachable_twitch_requires_review(self):
        e = Env().start("MATCH_START")
        m = e.new_manager(); e.tw.fail_next = ["500"]; m.recover()
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)

    def test_dry_run_safety(self):
        class Live: pass
        cfg = Config(dry_run=True)
        with self.assertRaises(RuntimeError):
            PredictionManager(cfg, StateMachine(Path(tempfile.mkdtemp()) / "s.json"), Live(), ManualScheduler())
        with self.assertRaises(RuntimeError):
            PredictionManager(Config(dry_run=False, live_confirmed=False), StateMachine(Path(tempfile.mkdtemp()) / "s.json"), Live(), ManualScheduler())
        with self.assertRaises(RuntimeError):
            PredictionManager(Config(dry_run=False, live_confirmed=True), StateMachine(Path(tempfile.mkdtemp()) / "s.json"), DryRunTwitch(), ManualScheduler())


class DodgeTests(unittest.TestCase):
    def test_agent_select_after_match_found_is_ignored(self):
        e = Env().start(); e.advance(30); e.mgr.on_confirmed("AGENT_SELECT")
        self.assertEqual(len(e.tw.preds), 1)

    def test_agent_select_alone_can_start(self):
        e = Env().start("AGENT_SELECT")
        self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)

    def test_match_found_again_cancels_and_restarts(self):
        e = Env().start(); first = list(e.tw.preds.values())[0]
        e.advance(75); e.mgr.on_confirmed("AGENT_SELECT"); e.mgr.on_confirmed("MATCH_FOUND")
        preds = list(e.tw.preds.values())
        self.assertEqual(len(preds), 2)
        self.assertEqual(first["status"], "CANCELED")
        self.assertEqual(preds[1]["status"], "ACTIVE"); self.assertEqual(preds[1]["prediction_window"], 60)
        self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)
        self.assertEqual(e.sm.data["prediction_id"], preds[1]["id"])
        self.assertEqual(e.mgr.voting_seconds_left(), 60)               # fresh 60 s countdown
        self.assertEqual(e.sched.pending().count("window_end"), 1)      # no duplicate timers

    def test_requeue_during_voting_window_also_restarts(self):
        e = Env().start(); e.advance(25); e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual([p["status"] for p in e.tw.preds.values()], ["CANCELED", "ACTIVE"])

    def test_same_banner_not_a_dodge(self):
        e = Env().start(); e.advance(6); e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual(len(e.tw.preds), 1); self.assertEqual(e.sm.state, State.PREDICTION_ACTIVE)

    def test_old_prediction_is_never_auto_canceled(self):
        e = Env().start(); e.lock(); e.advance(900)
        e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)
        self.assertEqual(list(e.tw.preds.values())[0]["status"], "LOCKED")

    def test_cancel_failure_no_second_prediction(self):
        e = Env().start(); e.advance(60)
        e.tw.fail_next = ["500"]; e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual(len(e.tw.preds), 1); self.assertEqual(e.sm.state, State.MANUAL_REVIEW)

    def test_can_be_disabled(self):
        e = Env(restart_on_requeue=False).start(); e.advance(60); e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual(len(e.tw.preds), 1); self.assertNotIn("MATCH_FOUND", e.mgr.wanted_screens())

    def test_requeue_then_resolves_new_one(self):
        e = Env().start(); e.advance(60); e.mgr.on_confirmed("MATCH_FOUND")
        e.lock(); e.advance(900); e.mgr.on_confirmed("VICTORY")
        p1, p2 = list(e.tw.preds.values())
        self.assertEqual((p1["status"], p2["status"]), ("CANCELED", "RESOLVED"))
        self.assertEqual(p2["winning_outcome_id"], p2["outcomes"][0]["id"])



class LateDodgeTests(unittest.TestCase):
    def test_late_dodge_restarts(self):
        e = Env().start(); e.lock()   # voting already locked
        e.advance(23 + 240)                                   # dodge at ~85 s, then a 4 minute queue
        e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual([p["status"] for p in e.tw.preds.values()], ["CANCELED", "ACTIVE"])
        self.assertEqual(e.mgr.voting_seconds_left(), 60)
    def test_beyond_max_age_goes_to_manual_review(self):
        e = Env().start(); e.lock(); e.advance(620)
        e.mgr.on_confirmed("MATCH_FOUND")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW); self.assertEqual(list(e.tw.preds.values())[0]["status"], "LOCKED")

class DrawTests(unittest.TestCase):
    def _locked(self, **kw):
        return Env(**kw).start().lock().advance(900)
    def test_draw_cancels_and_refunds(self):
        e = self._locked(); e.mgr.on_confirmed("DRAW")
        p = list(e.tw.preds.values())[0]
        self.assertEqual(p["status"], "CANCELED"); self.assertIsNone(p["winning_outcome_id"])
        self.assertEqual(e.sm.state, State.COOLDOWN)
        e.sched.fire("cooldown"); self.assertEqual(e.sm.state, State.IDLE)
    def test_draw_manual_policy(self):
        e = self._locked(draw_policy="manual"); e.mgr.on_confirmed("DRAW")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW); self.assertEqual(list(e.tw.preds.values())[0]["status"], "LOCKED")
    def test_draw_too_early_is_ambiguous_not_acted_on(self):
        e = Env().start(); e.lock(); e.advance(60)
        e.mgr.on_confirmed("DRAW")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW); self.assertEqual(list(e.tw.preds.values())[0]["status"], "LOCKED")
    def test_draw_cancel_failure_goes_to_review(self):
        e = self._locked(); e.tw.fail_next = ["500"]; e.mgr.on_confirmed("DRAW")
        self.assertEqual(e.sm.state, State.MANUAL_REVIEW)
    def test_draw_ignored_when_idle(self):
        e = Env(); e.mgr.on_confirmed("DRAW"); self.assertEqual(len(e.tw.preds), 0)
        e.mgr.on_confirmed("MATCH_FOUND"); self.assertIn("DRAW", e.mgr.wanted_screens())

if __name__ == "__main__": unittest.main()
