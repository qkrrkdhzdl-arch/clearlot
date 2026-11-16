"""Procedural bottle frames used to bootstrap the ClearLot classifier.

Why synthetic data: the demo has to run offline and a student cannot collect
thousands of real bottle photographs in one night. This generator produces the
*training* distribution (geometry, glass tint, transparency, label placement,
lighting, background) with exact labels; `ml/data/labels.csv` holds a small,
hand-labelled set of real photographs that is used only to measure transfer.

The generator is deliberately transparent about what it can and cannot do:
it models colour, transparency, silhouette and glare — not product branding or
liquid level. `ml/report.md` reports real-photo accuracy next to synthetic
accuracy for exactly this reason.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter

W, H = 256, 320
SS = 3  # supersampling factor

# hue, saturation, value window per class. Flint is nearly neutral: its "hue"
# is a faint cyan-green cast, which is exactly why it is the hard class.
CLASSES = ("flint", "green", "amber")
TINTS = {
    "flint": dict(hue=(140, 215), sat=(0.04, 0.20), alpha=(0.12, 0.28)),
    "green": dict(hue=(92, 158), sat=(0.50, 0.88), alpha=(0.62, 0.88)),
    "amber": dict(hue=(16, 42), sat=(0.62, 0.95), alpha=(0.72, 0.94)),
}

BACKGROUNDS = [
    (232, 230, 224), (214, 214, 212), (196, 198, 196), (168, 166, 160),
    (120, 118, 112), (92, 96, 98), (250, 249, 246), (60, 62, 64),
    (188, 176, 158), (150, 160, 150),
]


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    c = v * s
    x = c * (1 - abs(((h / 60.0) % 2) - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return tuple(int(round((ch + m) * 255)) for ch in (r, g, b))


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def render(cls: str, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    tint = TINTS[cls]
    hue = rnd.uniform(*tint["hue"])
    sat = rnd.uniform(*tint["sat"])
    alpha = rnd.uniform(*tint["alpha"])
    value = {"flint": rnd.uniform(0.80, 1.0),
             "green": rnd.uniform(0.26, 0.58),
             "amber": rnd.uniform(0.28, 0.56)}[cls]
    glass = hsv_to_rgb(hue, sat, value)
    bg = rnd.choice(BACKGROUNDS)
    bg = tuple(max(0, min(255, c + rnd.randint(-14, 14))) for c in bg)

    w, h = W * SS, H * SS
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img, "RGBA")

    # ---- background texture: mild vertical gradient + camera-ish noise
    grad = Image.new("L", (1, h))
    top = rnd.randint(-18, 12)
    for y in range(h):
        grad.putpixel((0, y), max(0, min(255, 128 + int(top * (1 - y / h)))))
    shade = Image.merge("RGB", (grad, grad, grad)).resize((w, h))
    img = Image.blend(img, shade, 0.10)
    d = ImageDraw.Draw(img, "RGBA")

    # ---- geometry
    body_w = rnd.uniform(0.42, 0.60) * w
    body_h = rnd.uniform(0.46, 0.58) * h
    cx = w * rnd.uniform(0.46, 0.54)
    base_y = h * rnd.uniform(0.86, 0.94)
    bw, bh = body_w, body_h
    top_y = base_y - bh
    neck_w = bw * rnd.uniform(0.32, 0.46)
    neck_h = rnd.uniform(0.13, 0.20) * h
    shoulder_h = rnd.uniform(0.05, 0.09) * h
    neck_top = top_y - shoulder_h - neck_h
    corner = min(bw, bh) * 0.10

    # soft ground shadow
    sh_w = bw * rnd.uniform(1.05, 1.35)
    d.ellipse([cx - sh_w / 2, base_y - bh * 0.03, cx + sh_w / 2, base_y + bh * 0.06],
              fill=(0, 0, 0, rnd.randint(26, 54)))

    def body_shape():
        d.rounded_rectangle([cx - bw / 2, top_y, cx + bw / 2, base_y], radius=corner, fill=None)

    # silhouette built as one polygon so glass fills it as a single material
    left, right = cx - bw / 2, cx + bw / 2
    n_left, n_right = cx - neck_w / 2, cx + neck_w / 2
    sil = [
        (left, base_y - corner), (left, top_y + corner * 0.6),
        (left + bw * 0.06, top_y - shoulder_h * 0.4),
        (n_left - bw * 0.02, top_y - shoulder_h),
        (n_left, neck_top),
        (n_right, neck_top),
        (n_right + bw * 0.02, top_y - shoulder_h),
        (right - bw * 0.06, top_y - shoulder_h * 0.4),
        (right, top_y + corner * 0.6), (right, base_y - corner),
        (right - corner, base_y), (left + corner, base_y),
    ]
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(sil, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(SS * 0.7))

    # ---- glass = background seen through tinted, slightly darkened glass
    seen_bg = bg if rnd.random() < 0.75 else _mix(bg, (255, 255, 255), 0.35)
    inner = Image.new("RGB", (w, h), seen_bg)
    di = ImageDraw.Draw(inner)
    # liquid-ish darker band and the base thickness
    if rnd.random() < 0.6:
        ly = top_y + bh * rnd.uniform(0.25, 0.6)
        di.rectangle([0, ly, w, base_y], fill=_mix(seen_bg, glass, 0.35))
    di.rectangle([left, base_y - bh * 0.06, right, base_y], fill=_mix(seen_bg, glass, 0.55))
    img = Image.composite(Image.blend(inner, Image.new("RGB", (w, h), glass), alpha), img, mask)
    d = ImageDraw.Draw(img, "RGBA")

    # vertical specular band along one flank
    hi_x = left + bw * rnd.uniform(0.12, 0.26)
    hi_w = bw * rnd.uniform(0.05, 0.11)
    d.rounded_rectangle([hi_x, top_y + bh * 0.04, hi_x + hi_w, base_y - bh * 0.05],
                        radius=hi_w / 2, fill=(255, 255, 255, rnd.randint(40, 110)))
    # rim highlights
    d.line([(n_left, neck_top + neck_h * 0.25), (n_left, top_y - shoulder_h * 0.5)],
           fill=(255, 255, 255, rnd.randint(50, 120)), width=max(2, int(SS * 1.6)))
    d.line([(left + 2, top_y + bh * 0.1), (left + 2, base_y - bh * 0.12)],
           fill=(255, 255, 255, rnd.randint(30, 90)), width=max(2, int(SS * 1.2)))

    # ---- label band (covers the centre of the body, never the flanks)
    if rnd.random() < 0.72:
        lab_h = bh * rnd.uniform(0.16, 0.30)
        lab_y = top_y + bh * rnd.uniform(0.30, 0.52)
        lab_col = rnd.choice([
            (250, 250, 246), (232, 226, 210), (26, 26, 28), (150, 30, 40),
            (20, 60, 120), (240, 196, 60), (30, 110, 70),
        ])
        d.rectangle([left + bw * 0.02, lab_y, right - bw * 0.02, lab_y + lab_h], fill=lab_col)
        if rnd.random() < 0.6:
            d.rectangle([left + bw * 0.10, lab_y + lab_h * 0.30, right - bw * 0.10, lab_y + lab_h * 0.44],
                        fill=_mix(lab_col, (0, 0, 0), 0.55))
    # ---- cap
    cap_col = rnd.choice([(200, 30, 40), (20, 20, 22), (200, 160, 40), (210, 210, 205), (60, 60, 68)])
    d.rectangle([n_left - bw * 0.01, neck_top - neck_h * 0.16, n_right + bw * 0.01, neck_top + 2],
                fill=cap_col)

    img = img.resize((W, H), Image.LANCZOS)

    # ---- camera-style post: exposure, white balance, noise, focus, jpeg-ish
    img = _photometric(img, rnd)
    return img


def _photometric(img: Image.Image, rnd: random.Random) -> Image.Image:
    import numpy as np
    arr = np.asarray(img, dtype=np.float32) / 255.0
    gain = rnd.uniform(0.86, 1.14)
    arr = arr * gain
    wb = np.array([rnd.uniform(0.94, 1.06), rnd.uniform(0.96, 1.04), rnd.uniform(0.94, 1.06)], np.float32)
    arr = arr * wb
    arr = np.clip(arr, 0, 1) ** rnd.uniform(0.9, 1.12)
    if rnd.random() < 0.45:
        noise = np.random.default_rng(rnd.randint(0, 2**31)).normal(0, rnd.uniform(0.006, 0.022), arr.shape).astype(np.float32)
        arr = np.clip(arr + noise, 0, 1)
    out = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    if rnd.random() < 0.25:
        out = out.filter(ImageFilter.GaussianBlur(rnd.uniform(0.4, 1.1)))
    # gentle vignette
    if rnd.random() < 0.5:
        import numpy as np
        a = np.asarray(out, dtype=np.float32)
        yy, xx = np.mgrid[0:H, 0:W]
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        vig = np.clip(1.0 - 0.16 * r ** 2, 0.6, 1.0)[..., None]
        out = Image.fromarray((a * vig).astype(np.uint8))
    return out


def write_dataset(out_dir: str, per_class: int, seed0: int = 1000) -> list[tuple[str, str]]:
    import os
    rows = []
    os.makedirs(out_dir, exist_ok=True)
    for ci, cls in enumerate(CLASSES):
        cdir = os.path.join(out_dir, cls)
        os.makedirs(cdir, exist_ok=True)
        for i in range(per_class):
            seed = seed0 + ci * 100000 + i
            path = os.path.join(cdir, f"syn_{i:04d}.jpg")
            render(cls, seed).save(path, quality=88)
            rows.append((path, cls))
    return rows


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rows = write_dataset("ml/data/synth", n)
    print(f"wrote {len(rows)} synthetic frames")
