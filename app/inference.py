"""Runtime inference: numpy only, weights loaded from the exported JSON.

The training pipeline (`ml/train.py`) and this module must agree bit for bit on
the feature layout; that is guaranteed by importing `ml.features` here rather
than re-implementing it.
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ml.features import extract, is_low_signal, load_frame  # noqa: E402

MODEL_PATH = os.path.join(ROOT, "ml", "model", "clearlot_model.json")
MARGIN_MIN = 0.45


@lru_cache(maxsize=1)
def load_model() -> dict:
    with open(MODEL_PATH) as f:
        m = json.load(f)
    m["_W"] = np.array(m["coef"], dtype=np.float64)
    m["_b"] = np.array(m["intercept"], dtype=np.float64)
    m["_mu"] = np.array(m["mean"], dtype=np.float64)
    m["_sd"] = np.array(m["scale"], dtype=np.float64)
    return m


def classify_bytes(data: bytes) -> dict:
    """Classify one photographed bottle.

    Returns a dict with the suggestion, the full probability vector, the
    top-two margin, and whether the frame is allowed to count automatically.
    """
    m = load_model()
    img = load_frame(data)
    feats = extract(img)
    z = ((feats - m["_mu"]) / m["_sd"]) @ m["_W"].T + m["_b"]
    z = z - z.max()
    p = np.exp(z)
    p = p / p.sum()
    order = np.argsort(p)[::-1]
    k = int(order[0])
    classes = m["classes"]
    margin = float(p[order[0]] - p[order[1]])

    reasons: list[str] = []
    low, why = is_low_signal(feats)
    reasons += why
    if margin < MARGIN_MIN:
        reasons.append(f"colour is ambiguous between {classes[order[0]]} and {classes[order[1]]}")

    return {
        "suggested_color": classes[k],
        "confidence": round(float(p[k]), 4),
        "margin": round(margin, 4),
        "probabilities": {c: round(float(p[i]), 4) for i, c in enumerate(classes)},
        "review_required": bool(low or margin < MARGIN_MIN),
        "review_reasons": reasons,
        "model_version": m["version"],
        "features": [round(float(x), 6) for x in feats],
        "top_two": [classes[int(order[0])], classes[int(order[1])]],
    }


def model_metrics() -> dict:
    m = load_model()
    return {
        "version": m["version"],
        "classes": m["classes"],
        "metrics": m["metrics"],
        "train": m["train"],
        "margin_min": MARGIN_MIN,
    }
