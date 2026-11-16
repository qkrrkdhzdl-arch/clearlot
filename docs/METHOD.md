# Method: what ClearLot measures, and how a pilot would prove it

This is the measurement document behind the theme paragraph. It is deliberately narrow: one metric,
one baseline, one way to check it.

---

## 1. The metric

> **Receiver-eligible share of incoming container glass** —
> the percentage of bottles a station grades that end up in a crate meeting a specific receiver's
> documented intake spec (colour purity ≥ X%, ≥ N bottles, contaminant checklist complete).

Two numbers are reported and never merged:

| Number | Meaning | Who can dispute it |
|---|---|---|
| **Projected eligibility** | This app graded the crate against the spec | Anyone with the receipt — it carries the spec version and every bottle |
| **Accepted mass** | The receiver took the crate | Only the receiver's weighbridge ticket |

The gap between them is the honest part of the design. A tool that reports "diverted" from its own
grading is reporting its own opinion.

## 2. The baseline it is compared against

The current path for a bar's bottles is a **3-mix crate**: all colours together, no specification, no
record. Container makers can substitute some 3-mix, but most of it falls below the colour purity a
bottle-colour customer specifies, and in markets without beneficiation the low-grade fraction is
diverted to road aggregate or alternative daily cover (GPI Glass Recycling Guidebook). So:

- **Baseline**: bottles that reach a receiver *as container-grade cullet on a named spec*. From an
  unsorted bar stream this is close to zero, because nothing in the chain ever checks colour purity or
  writes it down — but "close to zero" is the claim, not "zero".
- **With ClearLot**: bottles in crates that pass a named spec, with a per-bottle trail.

The claim is not "we increased glass recycling by N%". It is narrower and harder to fake:
*of the glass that leaves this bar, this share now arrives as a crate a plant can actually use, and
here is the receipt for each one.*

## 3. How a pilot would verify it

A single bar, 20 nights:

1. **Week 1–2 (baseline).** Bottles leave as they do today. At the plant, each delivery is weighed and
   the receiving operator records whether it was accepted as container-grade cullet or diverted.
2. **Week 3–4 (treatment).** Same stream, graded with ClearLot. Crates are closed only when the spec
   is met, and each carries a receipt with the bottle hashes.
3. **Verification.** The plant scans the crate QR, weighs it, and either records acceptance against
   the receipt ID or rejects it with a reason. Acceptance rate is computed from *their* events, not
   from the app's projection.

Success would be: **the share of delivered glass accepted as container-grade cullet rises, and the
rejection reasons shrink to the ones the checklist cannot catch** (which is the next thing to model).
Failure would be visible too — a crate rejected on colour means the gate threshold is wrong; a crate
rejected on ceramics means the checklist is not being followed.

Sample size honesty: one bar, 20 nights, a few hundred bottles. That is enough to show the flow works
and to size the effect; it is not enough to claim a population-level recycling rate.

## 4. Assumptions behind the only modelled number in the UI

The station view shows "modelled cullet" and a CO₂e figure. Both are labelled as modelled and both
are computed from two visible constants:

| Assumption | Value | Why |
|---|---|---|
| Mass per bottle | 0.35 kg | A 500 ml container-glass bottle; adjustable in `app/main.py::dashboard` |
| CO₂e avoided per tonne of cullet | 185 kg | British Glass / Enviros LCA (2003): *"every tonne of glass that goes back to re-melt saves around 185 kg of carbon dioxide"* |
| Upper bound for context | 670 kg/t | FEVE cradle-to-cradle LCA (EU average) — the high end of published values |

185 kg/t is the conservative end of the published range and is shown with the range beside it. The
spread between 185 and 670 is not noise: 185 assumes moderate cullet substitution and a UK grid, while
FEVE's 670 is cradle-to-cradle with high cullet share in an efficient furnace. Using the low end keeps
the number defensible. No figure here is presented as a measured outcome of this prototype.

## 5. What is intentionally *not* claimed

- No total-waste-diverted figure. The app only sees what a station captures.
- No "recycling rate" for a city, region or country.
- No claim that a colour-sorted crate *was* remelted. That is the receiver's event, and the receipt
  says so: `projected` → `recorded` → `verified` are separate rows.
- No claim that mixed-colour cullet cannot be recycled at all. It can be remelted into non-container
  products; the constraint is on using it to make *new containers* at any meaningful rate.

## Sources

- Glass Packaging Institute, [Facts About Glass](https://www.gpi.org/facts-about-glass) and
  [Glass Recycling Guidebook](https://www.gpi.org/sites/default/files/content-files/Lists%20and%20Resources/Glass%20Recycling%20Guidebook.pdf)
- FEVE, [Glass recycling is colorful — statement on the recyclability of coloured glass bottles](https://feve.org/glass-recycling-is-colorful/) (2019)
- FEVE / PE International LCA, [Why glass always has a happy CO₂ ending](https://feve.org/wp-content/uploads/2016/04/FEVE-brochure-Recycling-Why-glass-always-has-a-happy-CO2-ending-.pdf)
- British Glass / Enviros, [Glass recycling — life cycle carbon dioxide emissions](https://www.britglass.org.uk/sites/default/files/Glass-recycling-life-cycle-carbon-dioxide-emissions-report.pdf) (2003)
