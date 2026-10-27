# StitchCheck PWA: frontend spec

The phone app for the checker and the supervisor. It talks only to the backend API below (FastAPI, same origin). No API key ever reaches the browser. Product rules are in `docs/backend_spec.md`; this file is what the frontend needs.

## Ground rules

- **Process first.** Defects are about the process, not the person. Lead with machines and operations; never rank workers or use words like "worst operator". Alerts and actions are checks for the mechanic or the line (needle, tension, feed, handling).

- **AI drafts, checker decides.** Nothing counts as a defect until the checker taps Agree or Change.
- **Two-step result.** A photo first returns the crop and the classifier's label; Muse Spark's verdict follows a few seconds later. Show a "reviewing" state in between and never block the camera.
- **Labels in Telugu with English below.** The buyer report stays in English. Telugu strings are written and checked by Pruthvi; this spec lists the English text and keys.
- **Mobile first:** one hand, rear camera, large tap targets, works on a cheap Android phone over 4G. Installable PWA.
- **Demo data is labelled.** Any screen showing a demo session shows a visible "Demo data" badge.

## Data you will see

Defect labels: `good`, `skipped_stitch`, `broken_stitch`, `crooked_seam`, `puckering`, `stain_damage`, `open_seam`, plus `unknown_defect` from the AI. Show them as words ("Skipped stitch"), never as keys.

Parts: `collar`, `pocket`, `hem`, `side_seam`, `sleeve`, `placket`, `cuff`, `shoulder`.

Severity: `critical`, `major`, `minor`, `none`. Colour major and critical red, minor amber.

Machines are numbers 1 to 25. Operators are IDs such as `OP07`.

## Screens

### 1. Start day
Fields: date, style, fabric (A, B, C or free text), lot size, critical parts (default collar and placket), line plan (part, operator, machine per row; pre-filled template the supervisor edits), and the **AI mode**. Button: Start.
- AI mode: `GET /brains` → `[{brain, label, major_recall, good_flagged, sent_to_checker, available, spent_today_usd}]`. Show two cards with their measured numbers, e.g. "Low false alarm (Muse Spark): catches 92.9% of major defects, flags 9.5% of good seams" and "High sensitivity (Muse Spark Open): catches 94.4%, flags 20.2%". Default: Muse Spark. A footnote: "Measured on a public stitch dataset; if Muse Spark is unavailable, Muse Spark Open answers automatically."
- `POST /sessions` with `{date, style, fabric, lot_size, critical_parts, line_plan: [{part, operator_id, machine_id}], is_demo, brain}` → session with `id`, `brain` and `line_plan`. Change it later with `POST /sessions/{id}/brain` `{brain: "spark" | "open"}`.
- Then `POST /sessions/{id}/bundles` with `{bundle_no}` → `{id, bundle_no}`.

### 2. Inspect (the main screen)
Top bar: bundle number, garment counter ("Garment 12"), DHU so far.

**Garment flow:**
1. "Next garment": `POST /bundles/{bundle_id}/garments` with `{garment_no}` → `{id, garment_no, spot_check_part, photograph: [parts]}`. `photograph` is the list of seams to shoot for this garment, in order (critical seams, then the spot check if drawn). Show it as a checklist; the current part is highlighted.
2. The checker can also add a **suspicious seam**: pick any part, shoot it with `reason=suspicious`.
3. "Garment done": `POST /garments/{id}/complete` → `{status, new_alerts: [...]}`. Show new alerts as a banner (see Dashboard). Enable "Garment done" only when every listed seam has a decision.

**Shooting a seam:**
- Camera: rear camera, landscape hint, a guide line ("Hold the seam level, about 15 cm away").
- Upload: `POST /garments/{id}/photos`, multipart `image` (the file), `part`, `reason` (`critical`, `suspicious` or `spot_check`).
- Response is one of:
  - **Retake:** `{retake: "Move closer to the stitching", step1_seconds}`. Show the message, keep the camera open.
  - **Step 1:** `{inspection_id, review_status: "reviewing", crop_url, classifier_label, classifier_top2: [[label, prob], [label, prob]], scale_path, step1_seconds}`. Show the crop and "Classifier: Skipped stitch", then a "Reviewing..." spinner.
- Poll `GET /inspections/{inspection_id}` every second until `review_status == "done"` (stop after 30 s and show "Review slow, you can still decide").

**Result card** (when done), from the inspection:
- Crop image (`crop_url`); tap to see the photo with the seam SAM found tinted green (`overlay_url`, also returned in step 1), and the original (`photo_url`).
- Proposed label (`proposed_label`) and severity (`proposed_severity`), the one-line reason (`spark_verdict.reason`).
- Which brain answered: `spark_verdict.brain` ("Muse Spark" or "Muse Spark Open"); if `spark_verdict.fell_back` is true, a small note "Muse Spark was unavailable; Muse Spark Open answered". If `brain` is null, no AI review was possible and the card says "Classifier only: your call".
- If `needs_checker` is 1: a clear "Your call" badge (the AI and the classifier disagree, or the AI was unsure).
- Part chip: the part being inspected; if `part_chip` is set, show a second chip "SAM sees: Pocket" that the checker can tap to change the part (sends a Change decision with `part`).
- Operator and machine (`operator_id`, `machine_id`).
- Buttons: **Agree**, **Change**, **Not a defect**, **Retake**, and a **mic** button.
- An expandable **"How the AI decided"** row: `GET /agent-runs/{agent_run_id}` → `steps: [{call: {name, arguments}, output}]`, `final`, `brain`, `fell_back`, `latency_ms`, `tokens_in`, `tokens_out`, `cost_usd`. Show each tool call with its output (for a verdict: `classify_stitch` → top-2 labels with probabilities).

**Decisions:** `POST /inspections/{id}/decision` with `{action, label?, part?, severity?, operator_id?, machine_id?, via}`.
- `agree`: allowed only after the review is done (409 "still reviewing" otherwise).
- `change`: opens a picker for label (and optionally part, severity); changing the part re-maps operator and machine from the line plan.
- `not_a_defect`: records the seam as good.
- `retake`: discards the result; shoot again.
- `via` is `tap`, or `voice` when the values came from the voice sheet.
- Returns the updated inspection.

### 3. Voice correction sheet
Hold the mic button to record (WAV, M4A or WebM). Release to send:
- `POST /inspections/{id}/voice`, multipart `audio` → `{voice_note_id, transcript, proposed: {label?, part?, severity?, operator_id?, machine_id?}, ambiguous: [fields], label_candidates: [labels], agent_run_id}`.
- Show the transcript, then the proposed fields as editable chips. Every field in `ambiguous` is highlighted for the checker to fill. If `label_candidates` is not empty, show them as **one-tap buttons** ("Skipped stitch", "Broken stitch", "Open seam").
- Nothing is saved until the checker taps **Confirm**, which sends a `change` decision with the confirmed values and `via: "voice"`.

### 4. Dashboard
`GET /sessions/{id}/dashboard` (refresh every 10 s while open, and after every decision):
- `completed_garments`, `inspections`, `pending_decisions`
- `dhu` and `defective_pct`: the two big numbers. DHU = defects per hundred garments; defective % = garments with at least one defect.
- `by.machine`, `by.part`, `by.defect`: `{key: {defects, dhu}}`, most defects first. Show as bar lists; machines and operations lead the screen.
- `top_issues`: `[[machine, part, defect, count]]`.
- `supervisor_only.by_operator`: per-operator counts, shown only on a separate supervisor view (behind a tap, never on the main dashboard), for training, never as a ranking.
- `alerts`: trend alerts `{machine_id, defect, garment_ids, created_at, body_md}` ("3 skipped stitches on machine 7 in the last 10 garments"). `body_md` (the AI-drafted note with checks) may be empty for now.
- `ai_vs_checker`: `{agreed, changed, not_a_defect, sent_to_checker}`.

### 5. Report
- "Generate today's report": `POST /sessions/{id}/report` (takes about 10 s; show progress) → `{id, body_md, validator_ok, agent_run_id, errors}`.
- Render `body_md` as markdown. If `validator_ok` is 0, show a warning: "Some numbers could not be checked; review before sending."
- "How the AI wrote this": the agent trace (`GET /agent-runs/{agent_run_id}`), listing each tool call and its output.
- "Print / Save PDF": print-friendly styles; the browser saves the PDF.
- `GET /reports/{id}` reopens a saved report.

### 6. Audit (backend coming)
Start an AQL audit for the lot, see the sampled garment numbers, inspect them, and watch progress against Ac/Re for major and minor defects; status in progress, accept or reject. Endpoints will be `POST /sessions/{id}/audit` and `GET /sessions/{id}/audit`; payloads will be added here when built.

### 7. Demo (backend coming)
For visitors without garments: a gallery of real phone photos that run through the live pipeline, upload from the visitor's phone, a per-visitor copy of a seeded demo day, and a Reset button. Endpoints will be added here when built.

## Other endpoints

- `GET /health` → `{ok: true}`.
- `GET /sessions/{id}` → session and line plan.
- `GET /media/...` serves crops and photos (use the URLs the API returns; never build paths).

## Error handling

- 404: the item does not exist; 409: "still reviewing"; 422: invalid input (message in `detail`).
- 429: too many requests from this phone in the last hour (60 model calls); show "Please wait a few minutes".
- 503: no AI brain available (both down or today's spend cap reached); the photo result still works (classifier only), voice and reports show "AI unavailable, try later".
- Network loss mid-garment: keep the garment open and retry uploads; never mark a garment done without the server's reply.
