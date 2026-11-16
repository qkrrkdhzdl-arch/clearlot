# ClearLot

**Find the crate that can close.**
A colour-grading station for returnable glass, built for the *Rethinking Waste* theme track of
Arbiter Hacks V1.

ClearLot sits between a bar's bottle pile and a beneficiation plant: it photographs one bottle at a
time, grades its colour with a small model that is allowed to say *I don't know*, assembles
colour-pure crates against a receiver's intake spec, and issues a hash-chained handoff receipt that
a plant can check without trusting the app.

- **Live flow:** capture → grade → review queue → spec gate → checklist → receipt → QR
- **Stack:** Python 3.12 · FastAPI · SQLite · vanilla HTML/CSS/JS · numpy-only inference
- **No keys, no CDN, no paid API.** `pip install -r requirements.txt` and it runs offline.

---

## 1. The problem this targets

Glass is the one packaging material that can be remelted forever without losing quality — and colour
is a **primary quality constraint** on whether that happens, alongside ceramic and organic contamination.

- Container makers are **limited in how much mixed-colour cullet ("3-mix") they can use**; separating
  by colour is what lets new bottles meet the colour standards their customers specify.
  — [Glass Packaging Institute](https://www.gpi.org/facts-about-glass)
- *"From a recycling point of view, coloured glass is not suitable to produce flint bottles … once
  glass has been sorted by colour, there is no limitation … we are producing coloured bottles made of
  more than 90% cullet."* — [FEVE, 2019](https://feve.org/glass-recycling-is-colorful/). Sorted colour
  is the gate; ceramics, metals and organics still have to be dealt with separately.
- Where a market has no beneficiation capacity, low-grade 3-mix goes to **landfill road aggregate or
  alternative daily cover** instead of a furnace. — [GPI Glass Recycling Guidebook](https://www.gpi.org/sites/default/files/content-files/Lists%20and%20Resources/Glass%20Recycling%20Guidebook.pdf)
- Ceramics and heat-resistant glass are the classic killer contaminants in a cullet batch; metals,
  stones and gravel must be removed before batching. — GPI, as above.

The decision that decides all of this is made **at the sink, at 2am, by a person holding one bottle**.
A bar has no line to a glass plant, no vocabulary for "flint", and no record of what it sent.

ClearLot is the missing step between the bar and the plant: it makes the crate gradeable *before* it
leaves the building, and it leaves a trail that says who decided what.

### What it deliberately does not do

- It is **not** a material detector. Colour is the only thing the model claims. Ceramics, pyrex,
  metal and stones stay a human checklist item — the model is never asked, and never pretends.
- It does not claim a crate was *accepted*. Projected eligibility, operator-recorded handover and
  receiver-verified acceptance are three separate tiers in the data model and in the UI.
- It does not invent carbon numbers. The one modelled figure on the dashboard shows its assumptions
  inline (see `docs/METHOD.md`).

---

## 2. Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate      # optional
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# open http://127.0.0.1:8000
```

You get an empty crate. Two ways to fill it:

1. **Upload photos** of bottles you have — one bottle per photo, inside the guide.
2. **Sample photo replay**: append `?reel=amber_19.jpg,amber_20.jpg,flint_02.jpg,amber_18.jpg`
   to the URL and press *Capture bottle* to walk the bundled photographs through the real classifier.

To see the full three-lot station view (a closed green crate with a receiver reference, an open
flint crate, and a fresh amber crate):

```bash
curl -X POST localhost:8000/api/demo/seed -H 'Content-Type: application/json' \
     -d '{"spec_id":"spec_amber_v2"}'
# then open the returned lot:  http://127.0.0.1:8000/?lot=<lot_id>
```

### Where things live

| Path | What |
|---|---|
| `app/main.py` | FastAPI routes, lot scoring, the public receipt page |
| `app/db.py` | SQLite schema, receiver specs, immutability triggers |
| `app/inference.py` | numpy-only runtime inference (weights from JSON) |
| `app/receipts.py` | canonical JSON, SHA-256 chain, HMAC |
| `app/static/` | single-page front end, bundled fonts, sample photographs |
| `ml/features.py` | the feature extractor — shared by training *and* serving |
| `ml/train.py` | training + honest evaluation, exports `ml/model/clearlot_model.json` |
| `ml/synth.py` | procedural bottle renderer that bootstraps the training set |
| `ml/report.md` | the generated report used in section 4 below |
| `demo/record_demo.py` | drives a real browser and records `demo/out/demo.mp4` |
| `docs/METHOD.md` | measurement definition, assumptions, pilot design |

---

## 3. How the flow works

```
photo ──▶ normalise (EXIF, letterbox 256×320, illuminant neutralisation)
      ──▶ features (3 regions × 13, + 4 global: hue histogram, chromatic ratio, saturation, value)
      ──▶ logistic regression ──▶ {flint, green, amber} + probability + top-two margin
      ──▶ gate:  margin ≥ 0.45 and the frame is readable?
             ├─ yes → auto-tag  (label_source = model)
             └─ no  → review queue  (blocks closure until a human decides)
      ──▶ lot score vs receiver spec: colour purity, minimum count, checklist
      ──▶ close (BEGIN IMMEDIATE, server-side re-check) → receipt
      ──▶ receipt: canonical JSON + SHA-256 + HMAC + chain to the previous receipt
```

Three design choices carry most of the argument:

1. **The model can refuse.** A linear colour model that always answers is a liability in a
   contaminated stream. The gate turns uncertainty into a work item instead of a wrong label.
2. **The gate is load-bearing.** A pending bottle *blocks the close*, server-side, inside the
   transaction. The UI cannot talk its way past it, and neither can a curl call.
3. **The receipt is checkable by the other party.** Hash alone is rewriteable by whoever owns the
   database, so the hash is also HMAC'd with a key stored outside SQLite, and each receipt links to
   the previous one. Tamper with a bottle row and the hash breaks; rewrite the chain and the HMAC
   breaks.

---

## 4. The model, and how well it actually works

The classifier reads **colour family only**. It never sees an object detector, so the operator frames
one bottle in the guide. Features come from three regions (neck and both flanks of the body,
deliberately avoiding the label):

- 7-bin saturation-weighted hue histogram per region
- chromatic coverage and ratio, median / p90 saturation, median value
- saturation-weighted circular mean hue (sin, cos)
- whole-frame chromatic ratio, saturation, value, dark-mass ratio

Before extraction, the frame's border is used as a grey-world reference to neutralise the
illuminant. That single step moved held-out real-photo accuracy from **0.74 → 0.87**: a green soju
bottle shot under a warm bar lamp stops drifting into amber once the lamp is removed.

### Data

| Set | Count | Use |
|---|---|---|
| Synthetic frames (`ml/synth.py`) | 1,008 | training (bootstraps the decision boundary) |
| Real photographs (Wikimedia Commons, CC0 / PD / CC BY / CC BY-SA) | 36 | 13 train (+3 augmentations each), **23 held out** |
| Held-out split | by *source image*, never by augmented row | reported below |

The synthetic set exists because a student cannot photograph thousands of bottles in one night. It is
not a claim about the real world — which is why the real-photo holdout is the headline number and the
synthetic holdout is shown only for completeness.

### Results (`ml/report.md`, reproducible with `python3 ml/train.py`)

| Split | n | macro F1 | accuracy |
|---|---|---|---|
| Synthetic holdout | 252 | 0.988 | 0.988 |
| **Real photographs, held out** (split by source image, no augmentation leakage) | **23** | **0.859** | **0.870** |
| *Baseline: colour-threshold rule, same features* | 23 | 0.331 | 0.565 |

Real-photo confusion matrix (rows = truth, columns = flint / green / amber):

```
 4  0  1
 0  4  2
 0  0 12
```

At runtime the operating point is **83% auto-tagged, 2 of 30 auto-tags wrong, 17% sent to review** on
the sample set. A frame is graded in about **12 ms** on one CPU core (decode, features and model), so the
throughput limit is the operator framing the bottle, not the model. The two wrong auto-tags are both bottles the colour rules genuinely struggle with (a
pale, low-saturation flask and a bottle shot against an orange wall) — they are visible in
`ml/report.md` rather than hidden.

### Honest limitations

- **23 held-out photographs is a pilot, not a benchmark.** Ten of them come from one source family.
  Treat macro-F1 0.859 as "this works on bottle-shaped objects on plain backgrounds", not as a
  product accuracy claim.
- **Synthetic pretraining does not transfer for free.** It gives the boundary its shape; real photos
  give it its position. The real-photo weight in training (×8) is a hand-tuned choice.
- **Transparent PET and clear glass look alike in a single unlit frame.** The app mitigates with the
  review gate, not with a claim.
- **Colour is not material.** Ceramic, pyrex and metal are checklist items, and always will be until
  there is a real detector.
- **No camera calibration.** The border-based illuminant estimate assumes a roughly neutral mat
  around the bottle, which is what the intake station provides and what a phone photo often does not.

Retraining:

```bash
pip install -r requirements-train.txt
python3 ml/train.py                    # writes ml/model/clearlot_model.json + ml/report.md
python3 ml/train.py --mode synth       # ablation: synthetic only
```

The repository ships the 36 letterboxed frames the model was trained on, so retraining works with no
network. To rebuild them from the original photographs:
`python3 ml/fetch_images.py && python3 -m ml.prepare_real && python3 -m ml.make_credits`
(the originals are not committed — `ml/data/raw/` is ignored — but every source URL and licence is in
`CREDITS.md`).

---

## 5. Why a plant could trust the handoff (and where trust stops)

`app/receipts.py` builds a canonical document (sorted keys, compact separators, no NaNs) containing:
the spec snapshot and version, every bottle's image SHA-256, the model's suggestion *and* its
probabilities, the human decision and reason, the checklist with actors, and the counts.

- `sha256` — the document's hash
- `hmac` — SHA-256 of that hash under a key written outside SQLite (`data/receipt.key`)
- `prev_hash` — links to the previous receipt, so receipts form a chain
- `GET /r/{receipt_id}` — a public page anyone with the QR can open; it re-verifies on every load

**Where trust stops:** a receiver-verified acceptance currently requires the operator to type the
plant's weighbridge reference. In production that event would be signed by the receiver, not entered
by the sender. That gap is stated in the UI (three tiers) rather than papered over.

---

## 6. Demo video

`demo/out/demo.mp4` (2:07, 1440×810, H.264). It is a recording of this app driving a real browser and
the real classifier — not a mockup, and not screen-captured motion graphics.

```bash
pip install playwright && python3 -m playwright install chromium
python3 demo/record_demo.py       # writes demo/out/demo_raw.webm, transcodes with ffmpeg if present
```

The script starts the server, seeds a crate, and drives the full path including a real blocked close.
The only thing it fakes is the *camera*: `?reel=` replays bundled photographs through the same
`POST /api/lots/{id}/capture` endpoint a phone would hit.

---

## 7. What I would do next

1. **A real detector for contaminants.** Colour is solved enough to ship; ceramics and pyrex are the
   actual failure mode of a cullet stream, and a second model on the same capture would close it.
2. **A camera with a fixed mat and a colour card.** Removes the illuminant guess and would let the
   app report a calibrated confidence instead of a relative one.
3. **Two-way receiver events.** Let the plant scan the crate QR and sign the acceptance, which turns
   the `verified` tier from typed evidence into a counter-signature.
4. **Crate-level sorting actions.** Today a stray is excluded; it should be routed to the crate it
   belongs to, and the two crate counts should update together.
5. **Pilot in one bar.** 20 nights, one crate a night, weighed at the plant — the design is in
   `docs/METHOD.md`.

---

## 8. Licence and credits

Code: MIT (`LICENSE`). Sample photographs: Wikimedia Commons under CC0 / Public Domain / CC BY /
CC BY-SA — per-file attribution in `CREDITS.md`, generated from `ml/data/raw/manifest.json`.
Bundled typefaces: Inter and JetBrains Mono, SIL Open Font License 1.1.
