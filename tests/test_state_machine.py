import tempfile, unittest, os
from pathlib import Path
from app.state_machine import StateMachine, State, InvalidTransition

class SMTests(unittest.TestCase):
    def setUp(self): self.p = Path(tempfile.mkdtemp()) / "state.json"
    def test_valid_path(self):
        sm = StateMachine(self.p)
        for s in (State.CONFIRMING_START, State.CREATING_PREDICTION, State.PREDICTION_ACTIVE,
                  State.WAITING_FOR_RESULT, State.RESOLVING, State.COOLDOWN, State.IDLE):
            sm.transition(s)
    def test_invalid_rejected(self):
        sm = StateMachine(self.p)
        with self.assertRaises(InvalidTransition): sm.transition(State.RESOLVING)
        with self.assertRaises(InvalidTransition): sm.transition(State.WAITING_FOR_RESULT) if False else sm.transition(State.COOLDOWN)
    def test_persist_and_reload(self):
        sm = StateMachine(self.p)
        sm.transition(State.CONFIRMING_START); sm.transition(State.CREATING_PREDICTION)
        sm.transition(State.PREDICTION_ACTIVE, prediction_id="abc")
        sm2 = StateMachine(self.p); self.assertTrue(sm2.load())
        self.assertEqual((sm2.state, sm2.data["prediction_id"]), (State.PREDICTION_ACTIVE, "abc"))
    def test_corrupt_file_safe(self):
        self.p.write_text("{nope"); sm = StateMachine(self.p)
        self.assertFalse(sm.load()); self.assertEqual(sm.state, State.IDLE)
    def test_listener(self):
        seen = []; sm = StateMachine(self.p, lambda s, r: seen.append(s))
        sm.transition(State.CONFIRMING_START); self.assertEqual(seen, [State.CONFIRMING_START])

if __name__ == "__main__": unittest.main()
