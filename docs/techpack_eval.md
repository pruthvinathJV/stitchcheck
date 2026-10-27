# Tech-pack reader: preregistered evaluation

Written and committed before any tech-pack extraction was run. Nothing here changes after the first extraction; a code fix (not a prompt or rule change) would be disclosed here and all seven sheets rerun.

## What is tested

Muse Spark reads a buyer's tech pack (one page, PDF or phone photo) and returns a draft inspection plan: style details, the stitching operations (part, stitch class, SPI target and tolerance, seam allowance, critical flag), the AQL requirements, buyer-specific severity rules (in any language), other notes, handwritten corrections and out-of-scope sections. Code then validates it: value ranges, ISO 2859-1 AQL values and inspection levels, which of them the app supports (major 2.5, minor 4.0, critical 0, level II), ISO 4915 stitch classes, and part names. Part names map to app parts only through an explicit synonym table; anything else is listed as unmapped. The supervisor confirms the plan in the app.

## Sheets

Seven one-page tech packs for a fictional buyer, "Sample Buyer Co.", made by the builder for this test. Each says on the page that it is a fictional sample. No real brand's document or layout is used. Files: `data/techpack/inputs/`.

| Group | Sheets | Input | Notes |
|---|---|---|---|
| Seen | TP-01 | PDF | clean digital |
| Seen | TP-02 | phone photo | page tilted about 20 to 30 degrees |
| Seen | TP-03 | phone photo | crease and shadow; an AQL value the app does not support |
| Seen | TP-04 | phone photo | a handwritten correction that overrides a printed value |
| Seen | TP-05 | phone photo | rules and notes in Telugu; a field marked TBC; an unsupported AQL value |
| **Unseen (headline)** | TP-06 | phone photo | not opened before the prompt was committed |
| **Unseen (headline)** | TP-07 | PDF | not opened before the prompt was committed |

**Disclosure.** The ground truth for TP-01 to TP-05 was read before the extraction prompt was written, so the "seen" group may be optimistic. The prompt contains no sheet-specific hint, but it was written knowing which cases the sheets test (a TBC field, a handwritten correction, Telugu rules, unsupported AQL values). TP-06 and TP-07 were copied into the repo without being opened, and their ground truth stays with the builder until extraction has run on all seven. **The unseen group is the headline result.**

## Fixed settings

- Model `muse-spark-1.3`, reasoning effort: API default, one request per sheet.
- Strict JSON schema output (`response_format: json_schema`, strict).
- Prompt sha256 prefix `1a7a6ea5b563d433`, schema `232c39013f3b19a3`, part synonym table `bf273b9094def177` (`src/stitchcheck/techpack/extract.py`).
- PDFs are sent as files; photos as JPEG with the long side at most 2048 px.
- One run per sheet (`scripts/techpack/run.py`). Retries only after an API or schema error, logged in the output.

## Commit order

(a) the sheets; (b) this document, the prompt, schema, validation rules, synonym table, run and scoring scripts, with nothing run; (c) the TP-01 to TP-05 ground truth; (d) extraction on all seven once the photos exist; (e) the builder hands over the TP-06 and TP-07 ground truth; then scoring (`scripts/techpack/score.py`).

## Metrics (per group, seen and unseen reported separately)

Scored fields per sheet:
- 6 header fields: buyer, style number, style name, fabric, colour, thread;
- 4 AQL fields: inspection level, critical, major, minor;
- for every operation in the ground truth: found or not, plus stitch class, SPI target, SPI tolerance, seam allowance and critical flag. Ground-truth rows are paired with extracted rows by app part (through the same synonym table), then by the most similar operation text.

A field is correct when it equals the ground truth after normalisation (lower case, punctuation and repeated spaces removed), numbers compared as numbers, and a field the sheet leaves blank or marks TBC is correct only if it comes back empty.

- **Headline 1: field accuracy** on the unseen group, with a 95% Wilson interval. Fields on one sheet are not independent, so the interval is narrower than it should be.
- **Headline 2: hallucinations**, counted: a value given where the sheet has none, an operation row that is not on the sheet, a severity rule that is not on the sheet. Target: zero.
- Secondary: severity-rule recall (same defect label and a severity the rule allows); missing fields flagged; unsupported values flagged by validation; handwritten corrections listed; lenient accuracy for text fields (token F1 at least 0.8); unmapped part names; latency and tokens per sheet.

## Limits

Seven sheets, all written by the builder in one template family, and two unseen. This shows whether the reader works and whether it invents values; it is not an estimate of accuracy on real buyers' documents.
