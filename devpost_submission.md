# ClearLot — Find the crate that can close

**Track:** Theme Track — Rethinking Waste
**Repo:** `https://github.com/<account>/clearlot` *(to be filled after the repo is pushed)*
**Video:** `https://youtu.be/<id>` *(2:07, uploaded after review)*
**Team:** solo (Park Gaon)

---

## Inspiration

Glass is the only packaging material that can be remelted forever without losing quality — and one of
the main things that stops it is a decision nobody writes down. Container makers can only use a limited
share of mixed-colour cullet, so a crate sorted by colour becomes new bottles while the same glass,
mixed, becomes road aggregate or landfill cover. That decision is made at the sink at 2am, by one
person holding one bottle, with no feedback loop and no record.

I started from a much smaller version of this: matching empty whisky bottles from bars with craft
makers who could turn them into vases and candle jars. That idea kept running into the same wall —
before a bottle can be *upcycled*, it has to be *sorted*, and before it can be sold to anyone, someone
has to be able to say what is in the box. ClearLot is the sorting step that made the rest possible.

## What it does

A capture station for a bar's bottle pile.

1. **Capture.** One bottle inside the guide. A small colour model grades it *flint / green / amber*
   with a probability and a top-two margin — and it is allowed to refuse.
2. **Refuse.** Below a 45% margin, or on a frame it cannot read, the bottle goes to a review queue
   with both plausible colours shown. A pending bottle **blocks crate closure server-side**, inside
   the transaction. A guess never closes a crate.
3. **Grade the crate.** Bottles accumulate against a named receiver spec: target colour, minimum
   purity, minimum count, and a checklist (caps, dry, no bags, **no ceramics or pyrex**). Purity is
   scored live against the spec's floor, and every blocker is stated in words.
4. **Close and hand over.** Closing issues a receipt: every bottle's image SHA-256, what the model
   said and with what probability, what the person decided and why, the checklist with actors, and
   the spec version. The receipt's hash chains to the previous receipt and is HMAC'd with a key
   stored outside the database, so a plant can verify the crate without trusting my app.
5. **Keep the evidence tiers apart.** *Projected* (my grading), *recorded* (the operator's word),
   *verified* (a receiver reference) are three separate rows. The station view never adds them
   together into one number that sounds better than it is.

The public receipt page at `/r/{id}` is what the QR code opens — it re-verifies its own hash on load
and says, in the sentence at the bottom, exactly what is and is not proven.

## How I built it

- **Back end:** Python 3.12 + FastAPI + SQLite. One page, no build step, no CDN, no keys. Closed lots
  reject further mutation through SQLite triggers, not just application checks.
- **Classifier:** a multinomial logistic regression on hand-built colour features — saturation-weighted
  hue histograms over three regions (neck and both flanks, deliberately avoiding the label),
  chromatic coverage, saturation and value percentiles, and a circular mean hue. Weights are exported
  to JSON so runtime inference needs only numpy; `ml/features.py` is imported by both training and
  serving so the vector cannot drift.
- **The step that actually mattered:** grey-world illuminant correction estimated from the frame
  border, using the intake mat as a neutral reference. It moved held-out real-photo accuracy from
  0.74 to 0.87 — a green soju bottle photographed under a warm bar lamp stops drifting into amber
  once the lamp is removed.
- **Training data:** a procedural bottle renderer (`ml/synth.py`) generates 1,008 frames with exact
  labels — geometry, glass tint, transparency, label placement, glare, noise. It bootstraps the
  boundary. 36 real photographs (Wikimedia Commons, CC0 / PD / CC BY / CC BY-SA) give it its
  position; 13 are added to training with augmentation, **23 are held out by source image** and never
  seen in training.
- **Front end:** vanilla HTML/CSS/JS, hand-written. Bundled Inter and JetBrains Mono so it renders
  identically offline. Numbers in the UI are monospaced on purpose: this is an instrument, not a
  dashboard.

### Numbers, with the awkward parts left in

| | macro F1 | accuracy |
|---|---|---|
| Held-out **real** photographs (n=23, split by source image) | **0.859** | 0.870 |
| Colour-threshold baseline on the same features | 0.331 | 0.565 |
| Synthetic holdout (n=252) | 0.988 | 0.988 |

Operating point on the sample set: **83% auto-tagged, 2 of 30 auto-tags wrong, 17% sent to review**.
Both wrong auto-tags are bottles the colour rules genuinely struggle with — a pale low-saturation
flask and a bottle shot against an orange wall. They are in `ml/report.md`, not hidden.

23 photographs is a pilot, not a benchmark, and the README says so in the section a judge reads first.

## The waste problem I targeted, and how I would measure whether it worked

Bars send their empty bottles out as mixed-colour cullet. Container-glass makers can only use a
limited share of mixed-colour ("3-mix") cullet, because the batch has to meet the colour their
customer ordered; once cullet is sorted by colour, the limit on how much recycled glass a coloured
bottle can contain effectively disappears (FEVE, 2019; Glass Packaging Institute). Sorting does not
remove the other contaminants — ceramics, metal and organics still have to be handled — but colour is
the gate that decides whether the material is even in the container loop. Where a market has no
beneficiation capacity, most of the 3-mix fraction is diverted to landfill road aggregate or
alternative daily cover instead of a furnace, so the last metre of a bottle's life is decided by
whether anyone sorted it, and almost nobody does.

**My metric is the receiver-eligible share of incoming container glass:** the percentage of bottles a
station grades that end up in a crate meeting a specific receiver's documented intake spec (colour
purity ≥ X%, ≥ N bottles, contaminant checklist complete). The baseline is the current path, where
almost none of a bar's glass arrives as container-grade cullet on a named spec, because nothing in the
chain checks colour purity or records it. To verify, I would run one bar for 20 nights: two weeks as-is,
two weeks with ClearLot, and compute the acceptance rate from **the plant's** events — they scan the
crate QR, weigh it, and record acceptance or a rejection reason against the receipt. The app's own
projection is reported separately and never counted as an outcome. Full design in `docs/METHOD.md`.

## Challenges

- **Honest machine learning with almost no data.** Ten photographs of one bottle are one specimen.
  Splitting by augmented row would have produced a beautiful, meaningless number, so the split is by
  source image and the real-photo holdout is the headline.
- **Making uncertainty load-bearing.** Early on the model's confidence only changed a colour chip.
  Now a low margin physically blocks the close inside `BEGIN IMMEDIATE`, which is the difference
  between a demo of a classifier and a system that protects a material stream.
- **A receipt that is worth something.** A hash alone is rewriteable by whoever owns the database.
  Adding an HMAC keyed outside SQLite, plus a chain to the previous receipt, makes tampering visible
  without pretending to be a blockchain.
- **Making an audit trail watchable.** The demo is the trail assembling: each capture stamps a row,
  the purity bar moves, a blocker appears, a blocker clears.

## What's next

1. **A contaminant detector.** Colour is solved enough to ship; ceramics and heat-resistant glass are
   the real failure mode of a cullet stream. A small vision model on the same capture would close the
   gap the checklist is currently papering over.
2. **A calibrated camera.** A fixed mat and a colour card would turn the relative confidence into a
   calibrated one, and remove the illuminant guess.
3. **Two-way receiver events.** Let the plant scan the QR and sign the acceptance, so the `verified`
   tier stops being typed evidence and becomes a counter-signature.
4. **Route strays instead of excluding them.** A flint bottle in an amber crate should move to the
   flint crate and update both counts.
5. **One-bar pilot**, then a cluster of bars feeding one beneficiation line.

## Built with

`python` · `fastapi` · `sqlite` · `numpy` · `pillow` · `scikit-learn` · `javascript` · `css` ·
`playwright` (demo recording) · `ffmpeg` · `qrcode`
