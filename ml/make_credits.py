#!/usr/bin/env python3
"""Generate CREDITS.md from the image manifest and the shipping file lists."""
import csv, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
man = json.load(open(os.path.join(HERE, "data", "raw", "manifest.json")))
samples = json.load(open(os.path.join(ROOT, "app", "samples", "manifest.json")))
labels = list(csv.DictReader(open(os.path.join(HERE, "data", "real", "labels.csv"))))

rows, missing = [], []
used = set()
for name, meta in sorted(samples.items()):
    used.add(meta["key"])
for r in labels:
    used.add(r["key"])

for key in sorted(used):
    m = man.get(key) or next((v for k, v in man.items() if k.startswith(key[:44])), None)
    if not m:
        missing.append(key); continue
    rows.append(m)

out = ["# Credits", "",
       "All sample photographs are third-party works used under the licence shown. They are shipped",
       "so the classifier can be retrained and the demo can be replayed offline.", "",
       "| set | title | author | licence | source |", "|---|---|---|---|---|",
       "| app/samples/, ml/data/real/ | " + " | ".join([
           "placeholder", "placeholder", "placeholder", "placeholder"]) if False else ""]
out = out[:-1]
for m in rows:
    title = m["title"].replace("File:", "").replace("|", "/")
    author = (m["author"] or "unknown").replace("|", "/")[:80]
    out.append(f"| {m['class']} | [{title}]({m['source']}) | {author} | {m['license']} | {m['source']} |")

out += ["", "## Typefaces", "",
        "- **Inter** — Rasmus Andersson, SIL Open Font License 1.1 (latin subset, `app/static/fonts/Inter-*.woff2`)",
        "- **JetBrains Mono** — JetBrains, SIL Open Font License 1.1 (`app/static/fonts/JetBrainsMono-*.woff2`)",
        "", "Both were fetched from the Fontsource distribution on jsDelivr and are bundled so the app runs",
        "with no network access.", "",
        "## Third-party code", "",
        "- Python: FastAPI, Uvicorn, Pillow, numpy, qrcode, python-multipart (runtime); scikit-learn (training only).",
        "- All are permissively licensed (MIT / BSD / Apache-2.0 / LGPL) and are installed by pip, not vendored.", ""]
open(os.path.join(ROOT, "CREDITS.md"), "w").write("\n".join(out))
print("wrote CREDITS.md with", len(rows), "images;", len(missing), "manifest keys missing")
if missing:
    print(missing[:5])
