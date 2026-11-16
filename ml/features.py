"""Colour features for the ClearLot intake classifier.

Shared by training (`ml/train.py`) and serving (`app/inference.py`) so that the
vector a judge sees in the UI is computed by exactly the same code that the
weights were fitted on.

Design notes
------------
The classifier never tries to *detect* a bottle. The operator frames one bottle
inside the capture guide, so the bottle occupies the middle of the frame and the
regions below are aimed at glass, deliberately avoiding the centre of the body
where a paper label usually sits.

Glass colour is a hue phenomenon, but it is a *low saturation* one: a flint
bottle is almost colourless and takes its hue from whatever is behind it. So
every hue statistic is weighted by saturation and always accompanied by the
chromatic coverage of the region. That way "no chromatic signal" is a feature
the model can learn instead of a phantom hue.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image, ImageOps

FRAME = (256, 320)  # (w, h) every image is normalised to before features

# x0, x1, y0, y1 as fractions of the frame. Neck + both flanks of the body.
REGIONS = {
    "neck": (0.34, 0.66, 0.06, 0.34),
    "left": (0.22, 0.38, 0.36, 0.86),
    "right": (0.62, 0.78, 0.36, 0.86),
}

# Hue edges in degrees. Chosen around the hues that matter for container glass:
# amber/brown 15-45, green 80-150, blue-green 150-190.
HUE_EDGES = (0.0, 20.0, 50.0, 90.0, 150.0, 200.0, 260.0, 360.0)

FEATURE_NAMES: list[str] = []


def _names() -> list[str]:
    if FEATURE_NAMES:
        return FEATURE_NAMES
    for r in REGIONS:
        FEATURE_NAMES.extend([
            f"{r}.valid_coverage",
            f"{r}.chromatic_ratio",
            *[f"{r}.hue_bin{i}" for i in range(len(HUE_EDGES) - 1)],
            f"{r}.sat_p50",
            f"{r}.sat_p90",
            f"{r}.val_p50",
            f"{r}.hue_sin",
            f"{r}.hue_cos",
        ])
    FEATURE_NAMES.extend(["global.chromatic_ratio", "global.sat_p50", "global.val_p50", "global.dark_ratio"])
    return FEATURE_NAMES


def rgb_to_hsv(arr: np.ndarray):
    """arr: float32 HxWx3 in 0..1 -> (h deg, s, v), all HxW float32."""
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = arr.max(axis=-1)
    mn = arr.min(axis=-1)
    delta = mx - mn
    s = np.zeros_like(mx)
    nz = mx > 1e-6
    s[nz] = delta[nz] / mx[nz]
    h = np.zeros_like(mx)
    d = delta > 1e-6
    # branch per dominant channel
    rm = d & (mx == r)
    gm = d & (mx == g) & ~rm
    bm = d & (mx == b) & ~rm & ~gm
    h[rm] = (60.0 * ((g[rm] - b[rm]) / delta[rm])) % 360.0
    h[gm] = 60.0 * ((b[gm] - r[gm]) / delta[gm]) + 120.0
    h[bm] = 60.0 * ((r[bm] - g[bm]) / delta[bm]) + 240.0
    return h, s, mx


def load_frame(data: bytes | str) -> Image.Image:
    """Decode any user image into the normalised 256x320 working frame.

    Aspect ratio is preserved and the remainder is filled with the neutral
    studio grey of the intake table, because squash-and-stretch would move the
    region windows off the bottle.
    """
    if isinstance(data, (bytes, bytearray)):
        img = Image.open(io.BytesIO(data))
    else:
        img = Image.open(data)
    img = ImageOps.exif_transpose(img).convert("RGB")
    return letterbox(img)


def letterbox(img: Image.Image, size=FRAME, bg=(238, 238, 234)) -> Image.Image:
    tw, th = size
    w, h = img.size
    scale = min(tw / w, th / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), bg)
    canvas.paste(img, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def _neutralise_illuminant(arr: np.ndarray) -> np.ndarray:
    """Grey-world correction estimated from the frame border.

    The intake station photographs a bottle on a neutral mat, so the outer band
    of the frame is the best available reference for the illuminant. Removing
    that cast is what lets a green bottle read green under warm bar lighting
    instead of drifting into amber. Gains are clipped so a strongly coloured
    border cannot invert the image.
    """
    H, W, _ = arr.shape
    t, b_ = max(2, int(H * 0.06)), max(2, int(H * 0.06))
    l_, r_ = max(2, int(W * 0.05)), max(2, int(W * 0.05))
    border = np.concatenate([
        arr[:t].reshape(-1, 3), arr[H - b_:].reshape(-1, 3),
        arr[:, :l_].reshape(-1, 3), arr[:, W - r_:].reshape(-1, 3),
    ])
    ref = border.mean(axis=0)
    target = float(ref.mean())
    if target <= 1e-3:
        return arr
    gain = np.clip(target / np.maximum(ref, 1e-3), 0.70, 1.45)
    return np.clip(arr * gain, 0.0, 1.0)


def extract(img: Image.Image) -> np.ndarray:
    """Return the feature vector for one normalised frame."""
    if img.size != FRAME:
        img = letterbox(img.convert("RGB"))
    arr = _neutralise_illuminant(np.asarray(img, dtype=np.float32) / 255.0)
    h, s, v = rgb_to_hsv(arr)
    H, W = v.shape
    nbins = len(HUE_EDGES) - 1
    feats: list[float] = []
    for _name, (x0, x1, y0, y1) in REGIONS.items():
        sl = (slice(int(y0 * H), int(y1 * H)), slice(int(x0 * W), int(x1 * W)))
        hs, ss, vs = h[sl], s[sl], v[sl]
        n_all = int(vs.size)
        v_all = float(np.percentile(vs, 50))
        # dark glass still carries colour, so the floor is deliberately low
        valid = (vs >= 0.04) & ~((vs > 0.96) & (ss < 0.10))
        n_valid = int(valid.sum())
        cov = n_valid / max(1, n_all)
        hv, sv, vv = hs[valid], ss[valid], vs[valid]
        hsin = hcos = 0.0
        hist = np.zeros(nbins)
        ratio = 0.0
        s50 = s90 = 0.0
        if n_valid >= 30:
            chrom = sv >= 0.15
            n_chrom = int(chrom.sum())
            ratio = n_chrom / n_valid
            s50 = float(np.percentile(sv, 50))
            s90 = float(np.percentile(sv, 90))
            if n_chrom >= 20:
                hc, sc = hv[chrom], sv[chrom]
                idx = np.clip(np.digitize(hc, HUE_EDGES) - 1, 0, nbins - 1)
                np.add.at(hist, idx, sc)
                hist = hist / (hist.sum() or 1.0)
                rad = np.deg2rad(hc)
                hsin = float(np.average(np.sin(rad), weights=sc))
                hcos = float(np.average(np.cos(rad), weights=sc))
        feats.extend([
            float(cov), float(ratio), *[float(x) for x in hist],
            s50, s90, v_all, hsin, hcos,
        ])
    # whole-frame statistics: how much colour is there at all, and how dark
    valid = (v >= 0.04) & ~((v > 0.96) & (s < 0.10))
    n_valid = max(1, int(valid.sum()))
    chrom = valid & (s >= 0.15)
    feats.extend([
        float(chrom.sum() / n_valid),
        float(np.percentile(s[valid], 50)),
        float(np.percentile(v[valid], 50)),
        float(((v < 0.18) & (s >= 0.05)).sum() / n_valid),
    ])
    out = np.asarray(feats, dtype=np.float64)
    assert out.shape[0] == len(_names()), (out.shape, len(_names()))
    return out


def is_low_signal(feats: np.ndarray) -> tuple[bool, list[str]]:
    """Cheap pre-model guard: refuse to classify a frame the camera never saw."""
    names = _names()
    idx = {n: i for i, n in enumerate(names)}
    reasons = []
    cov = np.mean([feats[idx[f"{r}.valid_coverage"]] for r in REGIONS])
    chrom = feats[idx["global.chromatic_ratio"]]
    if cov < 0.30:
        reasons.append("not enough of the frame is object")
    if chrom < 0.012:
        reasons.append("almost no chromatic signal")
    v_brightest = float(max(feats[idx[f"{r}.val_p50"]] for r in REGIONS))
    if v_brightest < 0.10:
        reasons.append("frame is too dark to grade colour")
    return bool(reasons), reasons


def to_json_vector(feats: np.ndarray) -> list[float]:
    return [round(float(x), 6) for x in feats]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def spread(v: np.ndarray) -> float:
    return float(math.sqrt(float(np.mean((v - v.mean()) ** 2))))
