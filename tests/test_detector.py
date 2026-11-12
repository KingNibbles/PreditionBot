import tempfile, unittest, os
import numpy as np, cv2
from app.config import TemplateSpec
from app.detector import Detector, ConfirmationTracker, Match

def pattern(seed, size=(640, 360)):
    rng = np.random.RandomState(seed)
    img = (rng.rand(size[1], size[0]) * 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (0, 0), 2)

class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.imgs = {n: pattern(i) for i, n in enumerate(["start", "win", "lose"], 1)}
        self.specs = []
        for n, scr in (("start", "MATCH_START"), ("win", "VICTORY"), ("lose", "DEFEAT")):
            p = os.path.join(self.d, n + ".png"); cv2.imwrite(p, self.imgs[n])
            self.specs.append(TemplateSpec(p, scr, (0.3, 0.3, 0.4, 0.3), 0.8))
        self.det = Detector(self.specs)

    def test_matches_correct_screen(self):
        for n, scr in (("start", "MATCH_START"), ("win", "VICTORY"), ("lose", "DEFEAT")):
            m, _, _ = self.det.classify(self.imgs[n])
            self.assertEqual(m.screen, scr); self.assertGreater(m.score, 0.95)

    def test_unrelated_frame_no_hit(self):
        m, top, _ = self.det.classify(pattern(99))
        self.assertIsNone(m); self.assertLess(top.score, 0.8)

    def test_threshold_respected(self):
        self.specs[0].threshold = 1.01
        d = Detector(self.specs)
        m, top, _ = d.classify(self.imgs["start"])
        self.assertIsNone(m); self.assertGreater(top.score, 0.9)

    def test_small_shift_tolerated_and_color_input(self):
        shifted = np.roll(self.imgs["start"], 5, axis=1)
        m, _, _ = self.det.classify(cv2.cvtColor(shifted, cv2.COLOR_GRAY2BGR))
        self.assertEqual(m.screen, "MATCH_START")

    def test_scaled_input_resized(self):
        big = cv2.resize(self.imgs["win"], (1280, 720))
        m, _, _ = self.det.classify(big)
        self.assertEqual(m.screen, "VICTORY")

class Clock:
    t = 0.0
    def __call__(self): return self.t

class TrackerTests(unittest.TestCase):
    hit = Match("MATCH_START", 0.95, 0.8, "t")
    def test_single_hit_never_fires(self):
        t = ConfirmationTracker(3, 60)
        self.assertIsNone(t.update(self.hit))
        self.assertIsNone(t.update(self.hit))
    def test_three_consecutive_fire_once(self):
        t = ConfirmationTracker(3, 60, Clock())
        r = [t.update(self.hit) for _ in range(3)]
        self.assertEqual(r, [None, None, "MATCH_START"])
    def test_miss_resets_streak(self):
        t = ConfirmationTracker(3, 60, Clock())
        t.update(self.hit); t.update(self.hit); t.update(None)
        self.assertIsNone(t.update(self.hit))
    def test_no_refire_while_visible(self):
        c = Clock(); t = ConfirmationTracker(2, 60, c)
        t.update(self.hit); self.assertEqual(t.update(self.hit), "MATCH_START")
        c.t = 500
        for _ in range(10): self.assertIsNone(t.update(self.hit))
        t.update(None); c.t = 600
        t.update(self.hit); self.assertEqual(t.update(self.hit), "MATCH_START")
    def test_cooldown_blocks_reappearing_screen(self):
        c = Clock(); t = ConfirmationTracker(2, 60, c)
        t.update(self.hit); t.update(self.hit); t.update(None); c.t = 10
        t.update(self.hit); self.assertIsNone(t.update(self.hit))
        c.t = 61; self.assertEqual(t.update(self.hit), "MATCH_START")
    def test_different_screen_resets(self):
        t = ConfirmationTracker(2, 60, Clock())
        t.update(self.hit); t.update(Match("VICTORY", .9, .8, "x"))
        self.assertIsNone(t.update(self.hit))

if __name__ == "__main__": unittest.main()
