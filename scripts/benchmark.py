"""Times the detector and reports CPU and RAM. Run: python -m scripts.benchmark [--seconds 30]"""
import argparse, os, sys, tempfile, time, statistics
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import TemplateSpec
from app.detector import Detector

ap = argparse.ArgumentParser(); ap.add_argument("--seconds", type=int, default=20)
ap.add_argument("--width", type=int, default=640); ap.add_argument("--height", type=int, default=360)
ap.add_argument("--interval", type=float, default=1.0); a = ap.parse_args()
rng = np.random.RandomState(0); d = tempfile.mkdtemp(); specs = []
for i, scr in enumerate(["MATCH_START"] * 3 + ["VICTORY"] * 2 + ["DEFEAT"] * 2):
    img = cv2.GaussianBlur((rng.rand(a.height, a.width) * 255).astype(np.uint8), (0, 0), 2)
    p = os.path.join(d, f"{i}.png"); cv2.imwrite(p, img); specs.append(TemplateSpec(p, scr, (.25, .25, .5, .25), .8))
det = Detector(specs, (a.width, a.height))
frame = cv2.cvtColor(cv2.GaussianBlur((rng.rand(a.height, a.width) * 255).astype(np.uint8), (0, 0), 2), cv2.COLOR_GRAY2BGR)
times, cpu0, t0 = [], time.process_time(), time.time()
while time.time() - t0 < a.seconds:
    s = time.perf_counter(); det.classify(frame, {"MATCH_START"}); times.append((time.perf_counter() - s) * 1000)
    time.sleep(a.interval)
wall, cpu = time.time() - t0, time.process_time() - cpu0
try:
    import resource; rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
except ImportError:
    rss = float("nan")
print(f"templates={len(specs)} size={a.width}x{a.height} scans={len(times)}")
print(f"scan ms: mean={statistics.mean(times):.2f} p95={sorted(times)[int(len(times)*.95)-1]:.2f} max={max(times):.2f}")
print(f"CPU (process, detector only, excl. OBS screenshot encode/decode): {100*cpu/wall:.2f}%")
print(f"peak RSS (Linux sandbox, incl. numpy+cv2): {rss:.0f} MB   <- re-measure on Windows via Task Manager")
