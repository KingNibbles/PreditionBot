import tempfile, threading, time, unittest, os
import numpy as np, cv2
from pathlib import Path
from app.config import Config, TemplateSpec
from app.detector import Detector
from app.dryrun import DryRunTwitch
from app.monitor import Monitor
from app.obs_client import ObsError
from app.prediction_manager import PredictionManager
from tests.fakes import ManualScheduler
from app.state_machine import StateMachine, State
from tests.test_detector import pattern

class FakeObs:
    def __init__(self, frames): self.frames, self.connected, self.connects, self.i = frames, False, 0, 0
    def connect(self):
        self.connects += 1
        if self.connects == 1: raise ObsError("OBS closed")
        self.connected = True
    def screenshot(self):
        if self.i == 2: self.i += 1; self.connected = False; raise ObsError("dropped")
        f = self.frames[min(self.i, len(self.frames) - 1)]; self.i += 1; return f
    def close(self): self.connected = False

class MonitorIntegration(unittest.TestCase):
    def test_obs_outage_then_one_prediction(self):
        d = tempfile.mkdtemp(); start = pattern(1)
        p = os.path.join(d, "s.png"); cv2.imwrite(p, start)
        det = Detector([TemplateSpec(p, "MATCH_START", (.3, .3, .4, .3), .8)])
        cfg = Config(dry_run=True, scan_interval_s=0.25, confirmations_required=3, obs_backoff_max_s=1)
        cfg.scan_interval_s = 0.25
        sm = StateMachine(Path(d) / "s.json"); tw = DryRunTwitch(); sched = ManualScheduler()
        mgr = PredictionManager(cfg, sm, tw, sched)
        frames = [pattern(50), pattern(51), pattern(52)] + [start] * 20
        obs = FakeObs(frames); mon = Monitor(cfg, obs, det, mgr); mon.backoff.maximum = 0.3
        mon.start(); t0 = time.time()
        while time.time() - t0 < 12 and sm.state != State.PREDICTION_ACTIVE: time.sleep(0.1)
        mon.stop(); mon.join(2)
        self.assertEqual(sm.state, State.PREDICTION_ACTIVE)
        self.assertEqual(len(tw.preds), 1)           # outage survived; only ONE prediction
        self.assertGreaterEqual(obs.connects, 3)      # initial fail + reconnect after drop

if __name__ == "__main__": unittest.main()
