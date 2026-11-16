#!/usr/bin/env python3
"""Normalise the hand-labelled real photographs into `ml/data/real/`.

Reads `ml/data/labels.csv` (key, label, use, note) where `key` is a manifest key
from `ml/fetch_images.py`. Each photo is letterboxed to the 256x320 working
frame with the same code path the server uses, so what the model saw in
evaluation is exactly what the camera pipeline produces at runtime.
"""
import csv, json, os

from PIL import Image

from ml.features import letterbox

HERE = os.path.dirname(os.path.abspath(__file__))
man = json.load(open(os.path.join(HERE, "data", "raw", "manifest.json")))
keys = list(man)
out = os.path.join(HERE, "data", "real")
os.makedirs(out, exist_ok=True)
rows = list(csv.DictReader(open(os.path.join(HERE, "data", "labels.csv"))))
done, missing = [], []
for r in rows:
    match = [k for k in keys if k == r["key"]] or [k for k in keys if k.startswith(r["key"][:44])]
    if not match:
        missing.append(r["key"]); continue
    k = match[0]
    im = Image.open(man[k]["file"]).convert("RGB")
    p = os.path.join(out, k.replace("/", "__") + ".jpg")
    letterbox(im).save(p, quality=92)
    r2 = dict(r); r2["path"] = p; r2["source"] = man[k]["source"]
    r2["license"] = man[k]["license"]; r2["author"] = man[k]["author"]
    done.append(r2)
with open(os.path.join(out, "labels.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(done[0].keys())); w.writeheader(); w.writerows(done)
print(f"prepared {len(done)} real frames, {len(missing)} unmatched")
if missing:
    print("unmatched:", missing)
