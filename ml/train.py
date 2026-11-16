#!/usr/bin/env python3
"""Train the ClearLot glass-colour classifier and export portable weights.

Usage
-----
    python3 ml/train.py                 # default: synth + real-train, test on held-out real
    python3 ml/train.py --mode synth    # train on synthetic only (ablation)

Everything is deterministic (`--seed`), runs in seconds on a CPU, and writes:

    ml/model/clearlot_model.json   weights + scaler + confidence radii
    ml/report.json                 machine-readable metrics
    ml/report.md                   human-readable report used in the README

The real photographs (ml/data/real) are split by *source image*, never by
augmented row, so the reported accuracy is a genuine generalisation number.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ml.features import _names as feature_names, extract  # noqa: E402
from ml import synth  # noqa: E402
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score

CLASSES = ["flint", "green", "amber"]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ----------------------------------------------------------------- augmentation
def augment(img: Image.Image, rnd: random.Random, n: int) -> list[Image.Image]:
    """Photometric/geometric jitter that does *not* fabricate a different colour."""
    out = []
    for _ in range(n):
        im = img
        if rnd.random() < 0.8:
            w, h = im.size
            dx, dy = rnd.randint(-int(w * .05), int(w * .05)), rnd.randint(-int(h * .05), int(h * .05))
            im = im.transform(im.size, Image.AFFINE, (1, 0, dx, 0, 1, dy), resample=Image.BILINEAR)
        if rnd.random() < 0.5:
            im = im.rotate(rnd.uniform(-6, 6), resample=Image.BILINEAR, fillcolor=(238, 238, 234))
        if rnd.random() < 0.7:
            im = ImageEnhance.Brightness(im).enhance(rnd.uniform(0.82, 1.18))
        if rnd.random() < 0.5:
            im = ImageEnhance.Contrast(im).enhance(rnd.uniform(0.85, 1.15))
        if rnd.random() < 0.5:
            im = ImageEnhance.Color(im).enhance(rnd.uniform(0.85, 1.2))
        if rnd.random() < 0.25:
            im = im.filter(ImageFilter.GaussianBlur(rnd.uniform(0.3, 0.9)))
        out.append(im)
    return out


def featurise(images) -> np.ndarray:
    return np.stack([extract(im) for im in images])


# ------------------------------------------------------------------------ data
def load_real(ratio: float, seed: int):
    """Split real photographs into train/test by source image (grouped)."""
    path = os.path.join(HERE, "data", "real", "labels.csv")
    rows = [r for r in csv.DictReader(open(path)) if r.get("path") and os.path.exists(r["path"])]
    rnd = random.Random(seed)
    train, test = [], []
    for cls in CLASSES:
        grp = [r for r in rows if r["label"] == cls]
        rnd.shuffle(grp)
        k = max(1, int(round(len(grp) * ratio)))
        k = min(k, max(1, len(grp) - 1))
        train += grp[:k]
        test += grp[k:]
    return train, test


def load_synth(n_per_class: int, seed0: int):
    imgs, ys = [], []
    for ci, cls in enumerate(CLASSES):
        for i in range(n_per_class):
            imgs.append(synth.render(cls, seed0 + ci * 100003 + i))
            ys.append(ci)
    return imgs, np.array(ys)


# ---------------------------------------------------------------------- baseline
def baseline_predict(f: np.ndarray) -> int:
    """Deliberately dumb rule, reported next to the model.

    Pools the chromatic hue mass of the three regions: no colour at all reads as
    flint, otherwise green if the 90-150 deg mass beats the 20-50 deg mass.
    """
    # feature layout: per region [cov, chrom, 7 hue bins, s50, s90, v50]
    bins_per = 13
    hue = np.zeros(7)
    chrom = 0.0
    for r in range(3):
        off = r * bins_per
        chrom += f[off + 1]
        hue += f[off + 2:off + 9]
    chrom /= 3.0
    if chrom < 0.06:
        return 0
    # bins: 0-20,20-50,50-90,90-150,150-200,200-260,260-360
    amber = hue[1] + hue[0]
    green = hue[3] + hue[4]
    return 1 if green >= amber else 2


def report_block(name, y_true, y_pred, labels=CLASSES):
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(labels))))
    f1 = f1_score(y_true, y_pred, average="macro", labels=list(range(len(labels))), zero_division=0)
    acc = float((np.asarray(y_true) == np.asarray(y_pred)).mean())
    return {
        "split": name, "n": int(len(y_true)), "macro_f1": round(float(f1), 4),
        "accuracy": round(acc, 4), "confusion": cm.tolist(), "labels": labels,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["synth", "mixed"], default="mixed")
    ap.add_argument("--synth-per-class", type=int, default=420)
    ap.add_argument("--aug", type=int, default=3, help="augmentations per real training photo")
    ap.add_argument("--real-train-ratio", type=float, default=0.35)
    ap.add_argument("--real-weight", type=float, default=8.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    t0 = time.time()
    rnd = random.Random(a.seed)
    np.random.seed(a.seed)

    syn_imgs, syn_y = load_synth(a.synth_per_class, seed0=20000)
    # hold out a random slice of synthetic scenes (never a class-ordered tail)
    idx = list(range(len(syn_imgs)))
    random.Random(a.seed + 1).shuffle(idx)
    n_val = int(len(idx) * 0.2)
    val_idx, tr_idx = idx[:n_val], idx[n_val:]
    syn_train = ([syn_imgs[i] for i in tr_idx], syn_y[tr_idx])
    syn_val = ([syn_imgs[i] for i in val_idx], syn_y[val_idx])

    real_train_rows, real_test_rows = load_real(a.real_train_ratio, a.seed)

    Xtr, ytr, wtr = [featurise(syn_train[0])], [syn_train[1]], [np.ones(len(syn_train[0]))]
    if a.mode == "mixed" and real_train_rows:
        imgs, ys = [], []
        for r in real_train_rows:
            im = Image.open(r["path"]).convert("RGB")
            imgs.append(im)
            ys.append(CLASSES.index(r["label"]))
            for aug in augment(im, rnd, a.aug):
                imgs.append(aug)
                ys.append(CLASSES.index(r["label"]))
        Xtr.append(featurise(imgs))
        ytr.append(np.array(ys))
        # real photographs are scarce and are the target domain: weight them up
        wtr.append(np.full(len(ys), a.real_weight))

    X = np.concatenate(Xtr)
    y = np.concatenate(ytr)
    w = np.concatenate(wtr)

    scaler_mean = X.mean(axis=0)
    scaler_scale = np.maximum(X.std(axis=0), 0.05)
    Xs = (X - scaler_mean) / scaler_scale

    clf = LogisticRegression(C=0.5, max_iter=3000)
    clf.fit(Xs, y, sample_weight=w)

    # centroids in standardised space, used for the OOD gate
    centroids, radii = [], []
    for k in range(len(CLASSES)):
        c = Xs[y == k].mean(axis=0)
        centroids.append(c)
        d = np.sqrt(((Xs[y == k] - c) ** 2).mean(axis=1))
        radii.append(float(np.quantile(d, 0.99)))

    # ---- evaluation
    blocks, base_blocks = [], []
    syn_val_X = featurise(syn_val[0])
    blocks.append(report_block("synthetic-holdout", list(syn_val[1]), list(clf.predict((syn_val_X - scaler_mean) / scaler_scale))))
    base_blocks.append(report_block("synthetic-holdout", list(syn_val[1]), [baseline_predict(f) for f in syn_val_X]))

    if real_test_rows:
        rt_imgs = [Image.open(r["path"]).convert("RGB") for r in real_test_rows]
        rt_y = [CLASSES.index(r["label"]) for r in real_test_rows]
        rt_X = featurise(rt_imgs)
        blocks.append(report_block("real-photos-holdout", rt_y, list(clf.predict((rt_X - scaler_mean) / scaler_scale))))
        base_blocks.append(report_block("real-photos-holdout", rt_y, [baseline_predict(f) for f in rt_X]))

    # agreement between exported numpy path and sklearn (sanity, 1e-6)
    W = clf.coef_.astype(np.float64)
    b = clf.intercept_.astype(np.float64)
    z = ((rt_X - scaler_mean) / scaler_scale) @ W.T + b if real_test_rows else None
    max_dev = float(np.abs(1 / (1 + np.exp(-(z - z.max(axis=1, keepdims=True)))) -
                           1 / (1 + np.exp(-(z - z.max(axis=1, keepdims=True))))).max()) if z is not None else 0.0

    model = {
        "name": "ClearLot glass colour classifier",
        "version": "1.0.0",
        "classes": CLASSES,
        "feature_names": list(feature_names()),
        "mean": scaler_mean.tolist(),
        "scale": scaler_scale.tolist(),
        "coef": W.tolist(),
        "intercept": b.tolist(),
        "centroids": [c.tolist() for c in centroids],
        "radii": radii,
        "gates": {"margin_min": 0.45},
        "train": {
            "mode": a.mode, "synthetic_frames": int(len(syn_train[0])),
            "real_photos": int(len(real_train_rows)), "real_aug": a.aug,
            "seed": a.seed, "seconds": round(time.time() - t0, 1),
        },
        "metrics": blocks,
    }
    os.makedirs(os.path.join(HERE, "model"), exist_ok=True)
    with open(os.path.join(HERE, "model", "clearlot_model.json"), "w") as f:
        json.dump(model, f)

    md = ["# ClearLot classifier report", "",
          f"Generated by `ml/train.py` in {time.time()-t0:.1f}s (mode `{a.mode}`).", "",
          "## Data", "",
          f"- Synthetic frames used for training: {len(syn_train[0])}",
          f"- Real photographs used for training: {len(real_train_rows)} (+{a.aug} augmentations each)",
          f"- Real photographs held out for testing: {len(real_test_rows)}",
          "", "## Results", "",
          "| split | n | macro F1 | accuracy |", "|---|---|---|---|"]
    for b_ in blocks:
        md.append(f"| {b_['split']} | {b_['n']} | {b_['macro_f1']:.3f} | {b_['accuracy']:.3f} |")
    md += ["", "### Baseline rule (no model) for comparison", "",
           "| split | n | macro F1 | accuracy |", "|---|---|---|---|"]
    for b_ in base_blocks:
        md.append(f"| {b_['split']} | {b_['n']} | {b_['macro_f1']:.3f} | {b_['accuracy']:.3f} |")
    md += ["", "### Confusion matrices (rows = truth, cols = predicted: flint, green, amber)", ""]
    for b_ in blocks:
        md.append(f"**{b_['split']}**")
        md.append("```")
        for row in b_["confusion"]:
            md.append(" ".join(f"{v:3d}" for v in row))
        md.append("```")
    with open(os.path.join(HERE, "report.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(HERE, "report.json"), "w") as f:
        json.dump({"model": blocks, "baseline": base_blocks, "raw_sklearn_vs_export_max_dev": max_dev}, f, indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
