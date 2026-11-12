"""Template matching on grayscale crops with OpenCV. CPU only, and it never touches OBS or Twitch."""
from __future__ import annotations
import logging, time
from dataclasses import dataclass
import cv2
import numpy as np

log = logging.getLogger(__name__)
cv2.setNumThreads(1)  # keep CPU use small and predictable


@dataclass
class Match:
    screen: str
    score: float
    threshold: float
    template: str

    @property
    def hit(self) -> bool:
        return self.score >= self.threshold


class LoadedTemplate:
    """A reference image cut down to its ROI, stored as grayscale at the scan resolution."""
    def __init__(self, spec, frame_size):
        self.spec = spec
        w, h = frame_size
        img = cv2.imread(str(spec.path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"Cannot read template: {spec.path}")
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)  # templates are full reference frames
        x, y, rw, rh = spec.roi
        self.box = (int(x * w), int(y * h), max(4, int(rw * w)), max(4, int(rh * h)))
        bx, by, bw, bh = self.box
        self.patch = np.ascontiguousarray(img[by:by + bh, bx:bx + bw])

    def score(self, gray_frame, search_margin=0.04) -> float:
        """Match the ROI patch inside a slightly larger search window (tolerates small UI shifts)."""
        h, w = gray_frame.shape[:2]
        bx, by, bw, bh = self.box
        mx, my = int(w * search_margin), int(h * search_margin)
        x0, y0 = max(0, bx - mx), max(0, by - my)
        x1, y1 = min(w, bx + bw + mx), min(h, by + bh + my)
        window = gray_frame[y0:y1, x0:x1]
        if window.shape[0] < bh or window.shape[1] < bw:
            return 0.0
        res = cv2.matchTemplate(window, self.patch, cv2.TM_CCOEFF_NORMED)
        return float(res.max())


class Detector:
    def __init__(self, specs, frame_size=(640, 360), default_threshold=0.80):
        self.frame_size = tuple(frame_size)
        self.templates = []
        for s in specs:
            if s.threshold is None:
                s.threshold = default_threshold
            self.templates.append(LoadedTemplate(s, self.frame_size))
        log.info("Detector loaded %d templates at %s", len(self.templates), self.frame_size)

    def to_gray(self, frame) -> np.ndarray:
        if frame.ndim == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if (frame.shape[1], frame.shape[0]) != self.frame_size:
            frame = cv2.resize(frame, self.frame_size, interpolation=cv2.INTER_AREA)
        return frame

    def best_per_screen(self, frame, only=None) -> dict:
        """Return {screen: Match} with the best-scoring template of each screen type."""
        gray = self.to_gray(frame)
        best = {}
        for t in self.templates:
            if only is not None and t.spec.screen not in only:
                continue
            sc = t.score(gray)
            m = Match(t.spec.screen, sc, t.spec.threshold, str(t.spec.path))
            if t.spec.screen not in best or sc > best[t.spec.screen].score:
                best[t.spec.screen] = m
        return best

    def classify(self, frame, only=None):
        """Return the single best *hit* (or None). Also returns the top raw score for the UI."""
        t0 = time.perf_counter()
        best = self.best_per_screen(frame, only)
        hits = [m for m in best.values() if m.hit]
        top = max(best.values(), key=lambda m: m.score, default=None)
        dt = (time.perf_counter() - t0) * 1000
        log.debug("scan %.1f ms %s", dt, {k: round(v.score, 3) for k, v in best.items()})
        if not hits:
            return None, top, dt
        # two different screens both hit: no decision
        if len(hits) > 1:
            hits.sort(key=lambda m: m.score, reverse=True)
            if hits[0].score - hits[1].score < 0.05:
                log.warning("Ambiguous scan: %s vs %s", hits[0], hits[1])
                return None, top, dt
        return max(hits, key=lambda m: m.score), top, dt


class ConfirmationTracker:
    """Requires N *consecutive* scans of the same screen, then latches until the screen goes away.

    A single matching screenshot can never fire. After firing, the same screen cannot fire again
    until it has disappeared (a miss or a different screen) AND the cooldown has elapsed, so a
    screen that stays visible can never produce a duplicate trigger.
    """
    def __init__(self, required=3, cooldown_s=120.0, clock=time.monotonic):
        self.required, self.cooldown_s, self.clock = required, cooldown_s, clock
        self.streak_screen, self.streak = None, 0
        self.latched = set()
        self.last_fired = {}

    def reset(self):
        if self.streak_screen:
            self.latched.discard(self.streak_screen)
        self.streak_screen, self.streak = None, 0

    def update(self, match):
        """Feed the latest Match (or None). Returns the confirmed screen name or None."""
        if match is None:
            self.reset(); return None
        if match.screen != self.streak_screen:
            self.reset()
            self.streak_screen = match.screen
        if match.screen in self.latched:
            return None
        self.streak += 1
        if self.streak < self.required:
            return None
        last = self.last_fired.get(match.screen)
        if last is not None and self.clock() - last < self.cooldown_s:
            return None  # still cooling down
        self.last_fired[match.screen] = self.clock()
        self.latched.add(match.screen)
        log.info("CONFIRMED %s (score %.3f, %d consecutive)", match.screen, match.score, self.required)
        return match.screen
