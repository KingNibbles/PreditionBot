"""Makes assets/icon.ico. Swap in your own icon to rebrand. Needs Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"

def draw(size=1024):
    S = size
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=(22, 26, 38, 255))
    m = int(S * 0.15); bar_h = int(S * 0.15); gap = int(S * 0.07)
    # two outcome bars (WIN teal ~ longer, LOSE red ~ shorter), like a prediction split
    top = int(S * 0.30)
    d.rounded_rectangle([m, top, int(S * 0.62), top + bar_h], radius=bar_h // 2, fill=(45, 226, 190, 255))
    d.rounded_rectangle([m, top + bar_h + gap, int(S * 0.45), top + 2 * bar_h + gap], radius=bar_h // 2, fill=(255, 84, 96, 255))
    # check mark in a circle, lower right
    cx, cy, r = int(S * 0.68), int(S * 0.70), int(S * 0.19)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
    w = int(S * 0.045)
    d.line([(cx - r * 0.5, cy + r * 0.02), (cx - r * 0.12, cy + r * 0.42), (cx + r * 0.55, cy - r * 0.38)],
           fill=(22, 26, 38, 255), width=w, joint="curve")
    return im

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True); big = draw()
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon_preview.png")
    print("wrote", OUT / "icon.ico")
