"""Save a template from OBS (or --from-file) and add it to config.json.
Example: python -m scripts.capture_templates --name victory --screen VICTORY --roi 0.34,0.42,0.31,0.17"""
import argparse, sys
from pathlib import Path
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import SCREEN_TYPES, check_template_name
from app.controller import Controller

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--screen", choices=SCREEN_TYPES, required=True)
ap.add_argument("--roi", default="0,0,1,1", help="x,y,w,h as fractions (crop to a distinctive UI element)")
ap.add_argument("--from-file")
ap.add_argument("--threshold", type=float, default=0.80)
a = ap.parse_args()

try:
    check_template_name(a.name)
except ValueError as e:
    sys.exit(str(e))
ctl = Controller()
frame = cv2.imread(a.from_file) if a.from_file else ctl.grab_frame()
if frame is None:
    sys.exit(f"Could not read {a.from_file}")
ctl.save_template(frame, a.name, a.screen, [float(x) for x in a.roi.split(",")], a.threshold)
print(f"Saved templates/{a.name}.png and added it to config.json.")
