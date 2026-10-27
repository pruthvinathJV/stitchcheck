# StitchCheck backend and agent spec

The backend turns the evaluated models into the product used on the line and in the hosted demo. It imports the same classifier, Muse Spark client, SAM client and voice module as the evaluation code, and runs the same configuration.

## 0. Principles

1. **AI drafts, checker decides.** Nothing is logged as a defect until the checker confirms it. Voice corrections are shown as a parsed entry and confirmed with a tap.
2. **Muse Spark is the inspector's brain.** Every photo verdict and every report is a Muse Spark call with tool use. The classifier, SAM, DHU and AQL are tools Spark calls or reads.
3. **Verifiable agent.** Reports never contain a number or ID that Spark typed itself: Spark writes placeholders that point at tool results, and the server fills them in. A validator rejects any bare number or ID outside a placeholder. Every agent call is stored with its tool calls and outputs (`agent_run`).
4. **The product runs the evaluated configuration.** 448 px, minimal reasoning effort, one good-seam reference (few-shot design (a)), the frozen hybrid rule and threshold, and the same `inspect()` function that arm D of the benchmark uses. One difference, stated in the README: the app's classifier is the same recipe retrained on all 11 fabrics (3-seed ensemble); each seed agrees with the 8-fabric benchmark ensemble on at least 90% of validation images.
5. **API keys stay on the server.** The browser never sees `MODEL_API_KEY`.

## 1. Domain model (SQLite)

A **session** is one working day with a **line plan**: which operator and machine do which operation. The part being inspected maps to its operation, which maps to the operator and machine, so the checker never types an operator ID.

| Table | Key fields |
|---|---|
| `session` | id, date, style, fabric (A/B/C or free text), lot_size, critical_parts (default: collar, placket), spot_check_rate (default 0.1), is_demo, status |
| `line_plan` | session_id, part (collar, pocket, hem, side_seam, sleeve, placket, cuff, shoulder), sequence_no, operator_id, machine_id |
| `bundle` | id, session_id, bundle_no |
| `garment` | id, bundle_id, garment_no, spot_check_part (nullable), status (open/complete), completed_at |
| `inspection` | id, garment_id, reason (critical/suspicious/spot_check/audit), photo_path, crop_path, sam_part, sam_part_conf, seam_found, classifier_top2 (json), spark_verdict (json), proposed_label, proposed_severity, final_label, final_part, final_severity, operator_id, machine_id, decision (pending/agree/changed/not_a_defect/retake), decided_via (tap/voice), config_hash, created_at |
| `voice_note` | id, inspection_id, audio_path, transcript, parsed (json), confirmed |
| `audit` | id, session_id, lot_size, plan (json), sampled_garment_nos (json), status (in_progress/accept/reject) |
| `alert` | id, session_id, machine_id, defect, garment_ids (json), body_md, agent_run_id, created_at |
| `report` | id, session_id, kind (daily/audit), body_template, body_md, validator_ok, agent_run_id, created_at |
| `agent_run` | id, kind (inspect/voice/report/alert), request (json), tool_calls (json), tool_outputs (json), final (json), latency_ms, tokens_in, tokens_out, cost_usd, created_at |

Every inspection keeps the photo, classifier output, Spark verdict and the checker's decision: this is the training-data loop.

## 2. Inspection workflow

For every garment the checker photographs:
- the **critical seams** set for the session (default: collar and placket);
- any **suspicious seam** they notice;
- on 1 garment in 10, chosen at random when the garment opens, one **spot-check seam** from the other parts.

Each garment counts as one unit for DHU once it is completed, whether or not a defect was found. The app pre-selects the part from the line-plan sequence (next critical part, then spot check); SAM's part suggestion overrides it only above 0.7.

## 3. Per-photo pipeline

`POST /garments/{id}/photos` (multipart image, part, reason)

1. Save the original. Apply EXIF rotation, strip GPS.
2. **SAM 3.1, in parallel:** "seam" plus at most the three tested parts (collar, pocket, hem). Part override above 0.7. No seam above the seam threshold → return `retake: "Move closer to the stitching"` and stop (no inspection row). Thresholds and their sources are in `docs/design.md`.
3. Crop tiles around the best seam mask, exactly as the phone-set evaluation does; 448 px for Spark, the classifier's input size for the ensemble.
4. **`inspect()`** (shared with benchmark arm D): one Muse Spark request with the reference image (a good seam from the session's fabric: Kadiri `{F}-1-5` for fabrics A/B/C, a training-fabric fallback otherwise) and a forced `classify_stitch` tool call that returns the ensemble's top-2 labels and probabilities. Strict JSON verdict:
   ```json
   {"verdict": "confirm|override|unknown_defect|ask_checker",
    "label": "good|skipped_stitch|broken_stitch|crooked_seam|puckering|stain_damage|open_seam|other",
    "severity_judgement": "major|minor|null",
    "reason": "one sentence a checker understands",
    "confidence": 0.0}
   ```
   The hybrid rule and routing are applied in code after the call (section 4).
5. Map the part to the line plan → operator_id, machine_id.
6. Insert the inspection with `decision = pending` and return what the UI needs: crop URL, mask overlay URL, part, proposed label, severity, reason, operator, machine, `needs_checker`.

If the Spark call fails, the classifier's top label is returned as `ask_checker`, so the line keeps moving.

Latency target: under 5 s per photo end to end, with per-step timings logged.

## 4. Hybrid rule and severity (frozen with the benchmark)

- Spark and classifier agree → that label.
- They disagree, or Spark says `ask_checker` → `ask_checker`. The proposed label shown to the checker is Spark's, except as below.
- **Major-defect protection:** if the classifier's top label is a major defect (skipped_stitch, broken_stitch, stain_damage) with probability ≥ t, Spark cannot turn it into good or crooked_seam. The proposal stays the classifier's label and the photo goes to the checker. t is chosen on validation data and frozen (`docs/design.md`).
- Spark's stated confidence is stored, not used for routing.
- **Severity comes from a fixed map:** skipped_stitch, broken_stitch, open_seam → major. Spark judges only stain_damage (major if visible on the face side), crooked_seam and puckering (major or minor by extent and visibility), and gives the reason.

## 5. Checker decision and voice

- `POST /inspections/{id}/decision` body `{action: agree|change|not_a_defect|retake, label?, part?, severity?, operator_id?, machine_id?}`. Only `agree` and `change` rows with a defect label count as defects. `retake` discards the photo and asks for a new one.
- `POST /inspections/{id}/voice` (audio) → transcription with the Telugu + English hint and a keyword list built from the label names and the session's line plan → Spark maps the transcript to a structured correction against the fixed lists and lists anything ambiguous. Returns `{transcript, proposed, ambiguous}`. Nothing is saved until the client sends `/decision`.

## 6. Counting and quality math (pure functions, unit-tested)

- **DHU** = confirmed defects / completed garments × 100 (defects, not defective garments), by session, operator, machine, part and defect.
- **Defective %** = completed garments with at least one confirmed defect / completed garments × 100, reported next to DHU.
- **AQL audit mode** (ISO 2859-1, single sampling, normal inspection, General Inspection Level II; major 2.5, minor 4.0; critical has acceptance 0 and is set by the checker by hand). Separate from the end-line DHU:
  - `aql_plan(lot_size)` returns the code letter and, **per severity**, the sample size and Ac/Re. Where the standard's arrows apply, major and minor can have different sample sizes.
  - Starting an audit draws the sample: random garment numbers from the lot. Only inspections of sampled garments count, and the audit counts **defects**.
  - Status: `in_progress`, `accept`, or `reject` as soon as a severity reaches its Re.
  - The full table is checked against at least two independent public ISO 2859-1 tables, cited in the code. Tests cover these rows and the arrow cases:

    | Lot size | Code | Sample | Major 2.5 Ac/Re | Minor 4.0 Ac/Re |
    |---|---|---|---|---|
    | 281–500 | H | 50 | 3/4 | 5/6 |
    | 501–1200 | J | 80 | 5/6 | 7/8 |
    | 1201–3200 | K | 125 | 7/8 | 10/11 |
    | 3201–10000 | L | 200 | 10/11 | 14/15 |

## 7. Agents

**Trend alert.** After each confirmed defect, a rule in code checks for the same defect 3 times on one machine within its last 10 completed garments. When it fires, Spark drafts a short alert for the supervisor using `get_checklist(defect)`, phrased as checks to make, never as blame.

**End-of-day report.** `POST /sessions/{id}/report`, Muse Spark with tools, multi-step:
- `get_dhu(scope)` for session / operator / machine / part / defect
- `get_top_issues(n)`: worst operator-part-defect combinations with counts
- `get_audit_status()`
- `get_examples(defect, n)`: photo IDs with checker-confirmed labels, for evidence links
- `get_checklist(defect)`: practical checks from a curated table (e.g. skipped stitch: needle size, needle damage, timing)

Spark writes the report with placeholders such as `{{dhu.operator.OP07}}` that refer to tool-result fields; the server fills them in. The validator rejects any bare number or ID outside a placeholder, apart from a short whitelist of constants (2.5, 4.0, 2859-1, Level II). On failure it retries once with the error list; if it fails again the report is saved with `validator_ok = false` and the UI shows a warning. Numbers are written in Western digits in both languages.

Report sections:
1. **Buyer summary (English):** lot, garments inspected, DHU, defective %, audit result if an audit was run, top 3 defects with counts, photo evidence links.
2. **Supervisor actions (Telugu and English):** for the worst operators and machines, the defect pattern and the checks to make.
3. **Data notes:** AI-proposed vs checker-confirmed counts, override rate.

Reports are HTML with print styles; the browser saves the PDF.

## 8. Demo mode

Visitors to the hosted URL have no garments and no tailor.
- **Seeded demo session:** one realistic day (line plan with about 8 operations, 6 bundles, about 40 garments, confirmed defects drawn from the Kadiri photos). Every visitor gets their own copy (by cookie), so resets never collide. Every demo screen is labelled **"Demo data"**.
- **Try a photo:** a gallery of Kadiri photos run through the real pipeline, plus upload from the visitor's phone.
- **Reset** restores the visitor's copy.
- **Spend protection:** per-IP rate limit, and a global cap of $10 of Spark calls per day, after which gallery photos are served from cache.

## 9. API surface for the frontend

`app/SPEC.md` lists screens, states, endpoints and payloads. Screens:
1. **Start day:** style, fabric, lot size, critical parts, line plan (pre-filled template).
2. **Inspect:** bundle and garment counter, the next seam to photograph, camera capture (rear camera, landscape hint), result card (crop with mask overlay, part chip, label, severity, reason), buttons Agree / Change / Not a defect / Retake, mic button, "Next garment".
3. **Voice confirm sheet:** transcript, parsed fields, confirm or edit.
4. **Dashboard:** live DHU, defective %, by operator, by machine, by defect, trend alerts.
5. **Audit:** start an audit, sampled garment numbers, progress against Ac/Re.
6. **Report:** generate, view, print to PDF.
7. **Demo:** gallery, reset.

UI labels in Telugu with English below; buyer report in English.

## 10. Stack and hosting

- FastAPI + SQLite + onnxruntime (the 3-seed ensemble exported as one graph that averages softmax), one service that also serves the built PWA.
- Hosted on Modal: scale to zero until Nov 5, one warm container Nov 5 to 8. One container only (single SQLite writer), with the volume committed after each write. Health-check endpoint; cold start measured.
- Config via environment: `MODEL_API_KEY`, thresholds, model paths. Weights live in private storage, never in git.

## 11. Tests

- Pure functions: DHU, defective %, AQL table rows and arrow cases, reject-as-soon-as-Re, line-plan mapping, spot-check draw, audit sample draw.
- Hybrid rule and severity map.
- Report validator: a report with an invented number fails; a valid report with placeholders passes.
- End to end with a fake Spark client and fake SAM: photo in → pending inspection → decision → DHU updates → report validates.

## 12. Build order

| When | Deliverable |
|---|---|
| Wed Oct 28 | Schema; pure functions (DHU, AQL, mapping) with tests; per-photo pipeline against the real APIs on 5 Kadiri pilot photos (the four smoke-test shirt photos if the pilot is late) |
| Thu Oct 29 | Decision and voice endpoints; dashboard endpoint; `app/SPEC.md` |
| Fri Oct 30 | Report agent and validator (may move to Saturday); Day 5 gate result sets the product mode (full or suggestion) |
| Sat–Sun | Trend alerts, demo seed, deploy, frontend |
| Mon Nov 2 | Hosted URL live, end to end on a phone |

Any change to a frozen evaluation setting, any new external service, or anything that would make the product differ from the evaluated configuration is agreed before it is made.
