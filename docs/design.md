# Design notes

## Evaluation data

**Image source: CC BY only.** Every training and evaluation image comes from StitchingNet-Seg (figshare, CC BY 4.0), which republishes StitchingNet images for good seams and seven defect classes. The split loader asserts that every row is CC BY (`load_split` in `src/stitchcheck/data/stitchingnet.py`). Nothing from the CC BY-NC release is used to train or evaluate anything (see "Licensing and puckering").

**Leave-2-fabrics-out.** StitchingNet has 11 fabrics, and each one uses a single thread type. The test set holds out Cotton (poly thread) and Oxford (core thread), both shirting fabrics; Linen is the validation fabric; the other 8 are for training. Validation is also by fabric because the images are sequential frames: the same physical defect often appears several times with small shifts, so a random image split would leak.

**Exact duplicates are removed before splitting** (134 images, all within one fabric and class). Split file: `data/splits/stitchingnet_v2.csv`.

**Classifier classes.** Five: good, skipped_stitch, broken_stitch, crooked_seam, stain_damage. The other three defect classes in StitchingNet-Seg (pinched fabric, thread sagging, overlapped stitch) are never trained on. Those images from the held-out fabrics form an "unseen defect" test (667 images). It measures (1) how often each system calls them good, and (2) how often Muse Spark flags them as an unknown defect.

**Set sizes.** Train 5,047, Linen validation 673, test 1,428 (5 classes on Cotton and Oxford), unseen-defect test 667.

**Muse Spark subset.** The Spark arms run on a fixed, seeded sample: 42 images per class per test fabric (420), plus 10 per unseen class per test fabric (60).

**Training fabrics.** Benchmark numbers on StitchingNet come from the classifier trained on 8 fabrics. The demo and the phone test set use it retrained on all 11 fabrics. Every reported number says which one produced it (8 or 11 fabrics).

**Per-fabric difficulty (holdout runs, if compute allows).** The classifier is also trained 8 more times, each time holding out one of the 8 training fabrics as validation and training on the other 7. This gives per-fabric recall without touching the test fabrics. It does not change the main split.

**Phone test set.** Sewn and photographed by a tailor in Kadiri, Andhra Pradesh, on locally bought shirting cotton. Three shirting cottons (white, dark, checks). Seven seam types: good, skipped, broken, crooked, puckering, stain, and open seam (an unseen defect). It is the only puckering test set: pairs `{fabric}-5-1` and `{fabric}-5-2` (6 pairs, 24 photos). Per fabric the tailor sews 5 good pairs, 3 pairs each of skipped stitch, broken stitch and puckering, and 2 pairs each of crooked seam, stain and open seam (20 pairs per fabric; 60 pairs, 240 photos). The last good pair and the last puckering pair of each fabric are reference pairs (below), so the test set is 54 pairs, 216 photos: per fabric 4 good, 3 skipped, 3 broken and 2 of every other type. Each sewn pair is photographed at four chalk marks (10, 25, 40 and 55 cm along the seam), top-down, in daylight. Photos arrive on WhatsApp as Documents: one text tag per pair, `{fabric}-{type}-{pair}` (e.g. `B-3-2`), followed by exactly 4 photos in chalk-mark order. Ingest warns on any tag without exactly 4 photos, duplicate tags, unknown tags and missing tags (`src/stitchcheck/phone/ingest.py`). Pair A-1-1 is sent first as a quality check and can be ingested and reviewed on its own. Broken stitch, crooked seam, stain and open seam are made at every chalk mark. Skipped stitch and puckering run along the whole seam, so a mark may show no visible defect; those photos are reviewed one by one and relabelled as good or rejected. All phone-set confidence intervals come from a bootstrap that resamples whole pairs, not single photos, because photos of the same pair are correlated. The phone set is never used for training or tuning.

**Reference pairs (separate from the test set).** Pairs `{fabric}-1-5` (good) and `{fabric}-5-3` (puckering): 6 pairs, 24 photos, same four chalk marks. The tailor tags them like any other pair; ingest routes them by tag into their own folder and manifest (`data/phone/ref/`), never into the test set, and they are never counted in test metrics. The split by tag was fixed before any photo arrived. Uses: good-seam references for few-shot Muse Spark on the Kadiri photos, the puckering few-shot examples (`{fabric}-5-3`, same fabric as the test photo), and calibrating the SAM seam threshold together with the shirt photos.

## Licensing and puckering

The StitchingNet authors declined use beyond the non-commercial license, so the classifier is trained only on CC BY data and covers four defects. Puckering is detected by Muse Spark using reference seams we sewed ourselves.

- **Classifier:** five classes (good, skipped_stitch, broken_stitch, crooked_seam, stain_damage), trained only on StitchingNet-Seg images (CC BY 4.0). An earlier six-class model that added puckering from the CC BY-NC release was trained on Oct 26 and deleted, with its results, when permission was declined. Its numbers are not reported.
- **Evaluation sets are CC BY only.** Puckering is removed from validation and from the Muse Spark subset. Needle mark and bobbin thread pulling up (not in StitchingNet-Seg) are removed from the unseen-defect test.
- **Puckering in the product and the benchmark:** Muse Spark alone, few-shot, with the tailor's puckering reference pair from the same fabric (`{fabric}-5-3`) and a good reference (`{fabric}-1-5`). It is evaluated only on the Kadiri phone pairs `{fabric}-5-1` and `{fabric}-5-2`. The QC agent logs it, assigns severity and reports it like any other defect.

## Metrics (fixed before results)

**Headline: recall on major defects.** Major = skipped_stitch, broken_stitch, stain_damage, and open_seam (phone set only), following standard garment AQL practice: these fail an audit wherever they appear. Crooked_seam and puckering count as defects in all other metrics but are not in the headline.

A major defect counts as caught if the system flags it as any defect class, as unknown_defect, or sends it to the checker. Exact-class recall is reported in the per-class table.

Because deferral counts as caught, two guards are reported next to the headline:

- **Auto-caught recall on majors:** the same recall, with "ask the checker" counted as a miss.
- **Checker workload:** the share of good seams that are flagged or deferred, which is the false-alarm cost in checker taps.

A system that defers everything shows a high headline, low auto-caught recall and a very high workload, so it cannot hide. Definitions are in `src/stitchcheck/eval/metrics.py`.

Also reported:

- per-class precision, recall and F1, and macro-F1
- binary defect-vs-good accuracy
- unseen-defect "called good" rate
- Muse Spark unknown_defect flag rate
- latency per photo
- cost per 100 photos, and per 100 garments at the product's photo rate: 2 critical seams per garment (collar, placket) plus a spot-check seam on 1 garment in 10, i.e. 2.1 photos per garment before any suspicious-seam photos; the assumption is stated next to the number

## Systems compared

- **(A)** Zero-shot Muse Spark.
- **(B)** Few-shot Muse Spark with one good-seam reference (k=1; design chosen on Linen, see Muse Spark settings), from the same fabric where possible.
- **(C)** Fine-tuned classifier (five classes, CC BY data): the 3-seed 8-fabric ensemble, epoch 12. Reported on the full test split (1,428 + 667 unseen) and on the Spark subset (420 + 60), so comparisons with the Spark arms are paired.
- **(D)** Hybrid: `inspect()` in `src/stitchcheck/spark/inspect.py`, the same function the app runs. Muse Spark with one good reference calls the classifier as a tool, and judges puckering itself few-shot (phone set only). Two views are reported: the auto label (disagreements count as ask_checker) and the label shown to the checker.
- **(E, optional)** Muse Spark Open (open weights) on Modal Endpoints, with the same prompts, image size, few-shot references and test subset as the Muse Spark arms. Whether it runs is decided on Day 4. Its results go in the main table only if it runs on the full subset; a partial run is reported separately and labelled as partial.

## Classifier selection (fixed before the search runs)

Training settings are chosen without the test fabrics, in two stages:

1. **Screen on Linen.** The classifier is trained with each candidate setting and scored on Linen validation macro-F1 (`scripts/train/sweep_stage1.sh`). Candidates: augmentation (brightness/contrast only; plus colour, grayscale and blur; TrivialAugment), learning rate, a 10x lower backbone learning rate, and architecture (EfficientNet-B0, MobileNetV3-Large, ConvNeXt-Tiny). The baseline is also rerun with a second seed, so a difference smaller than the run-to-run noise is not treated as real.
2. **Second seed.** The top 3 settings by their first-seed Linen macro-F1 are trained again with a second seed (the baseline already has one) (`scripts/train/stage2.py`, `stage2.sh`). The winner has the best mean Linen macro-F1 over the two seeds; ties go to the smaller model. The search then stops.

*Amended Oct 27, after Stage 1 had partly run and before any test-set run.* The original Stage 2 retrained the top 3 on three held-out training fabrics. Two runs of the baseline with different seeds scored 0.53 and 0.78 on Linen, so seed noise is larger than most differences between settings, and a second seed per setting was judged a better use of the remaining compute.

*Amended Oct 27, after the seed check and before any test-set run.* The 0.53 vs 0.78 gap was not a learning-rate or warm-up problem: ImageNet weights were loaded, training loss matched across seeds at every epoch, and the gap was there from epoch 2, not just at the last epoch. Re-estimating BatchNorm statistics on Linen images (no labels) brought both seeds to about 0.80, so the cause is BatchNorm statistics fitted to the training fabrics. Re-estimating them on the target fabric is not used, because it adapts the model to the evaluation images. Instead BatchNorm is kept at its ImageNet statistics during fine-tuning (`--freeze-bn`). Seed check on Modal, 3 seeds each, best-epoch Linen macro-F1:

| BatchNorm | seed 0 | seed 1 | seed 2 | mean | max - min |
|---|---|---|---|---|---|
| updated during training | 0.719 | 0.667 | 0.744 | 0.710 | 0.077 |
| frozen | 0.862 | 0.870 | 0.832 | 0.855 | 0.038 |

Frozen BatchNorm is the base setting for Stage 1 and Stage 2 (`scripts/cloud/jobs_stage1.txt`); only frozen-BatchNorm runs are ranked. ConvNeXt-Tiny has no BatchNorm, so the flag does not change it.

**Hardware.** Every reported classifier run trains from scratch in one process on one Modal NVIDIA A10G (`scripts/cloud/train_modal.py`), with images downloaded from figshare and checked against the manifest by md5. Earlier runs on a laptop GPU were used only for the diagnosis above and are not reported.

**Final models are 3-seed ensembles** (mean of softmax). The 8-fabric benchmark model is the winning setting with seeds 0, 1 and 2; its report gives each seed's Linen macro-F1 and the ensemble's.

*Fixed epoch count (added Oct 27, before any test-set run).* Picking each run's best epoch on Linen uses Linen twice, and the demo model has no Linen to pick on. So N = the median best epoch over every seed of the winning setting from the search, rounded to the nearest whole epoch (.5 rounds up). Every final model, the 3 benchmark seeds and the 3 demo seeds, is trained from scratch on the usual 12-epoch learning-rate schedule, stopped after epoch N, and the epoch-N weights are kept (`--stop-after N`; `scripts/train/stage2.py final`). Stopping inside the 12-epoch schedule, instead of shortening the schedule to N epochs, keeps the final models the same as the runs whose best epochs set N. The benchmark ensemble's Linen macro-F1 at epoch N is reported as optimistic, because the setting and N were both chosen on Linen. **N = 9**: the winning setting's two search runs both had their best Linen epoch at 9. The demo model is the winning setting on all 11 fabrics with seeds 0, 1 and 2. Safety check: each demo seed must agree with the benchmark ensemble on at least 90% of Linen predictions (`scripts/train/ensemble.py`). Linen is in the demo model's training data, so this is not a score, only a check that no seed went bad.

**Final models (Oct 27, before any test-set run; `results/classifier/ensemble.json`).** Benchmark seeds at epoch 9, Linen macro-F1: 0.944, 0.927, 0.880; 3-seed ensemble **0.943 (optimistic: the setting and N were chosen on Linen)**. Demo seeds' agreement with the benchmark ensemble on Linen: 94.2%, 94.1%, 94.2%, all above 90%, so the check passes. Two caveats:
- Training on the GPU is not bit-for-bit reproducible. The final seeds 0 and 1 repeat the search runs' settings and seeds, yet scored 0.944 and 0.927 at epoch 9 against 0.958 and 0.962 in the search, so the search score of 0.960 is partly luck.
- Epoch 9 sits where the learning rate is still well above its final value, and single-seed Linen F1 moves a lot from epoch to epoch there (final seed 2: 0.906, 0.784, 0.886, 0.881, 0.879 over epochs 5 to 9; seed 1: 0.962 at epoch 6, 0.773 at epoch 8). The seed spread at epoch 9 is 6.4 points; the ensemble absorbs most of it.

*Amended Oct 27, on validation results, before any test-set run: final models keep epoch 12.* All 6 final models (3 benchmark seeds, 3 demo seeds) train the full 12-epoch schedule and keep the last epoch (`--stop-after 12`, run tags `final12-seed*`). The epoch-9 models above are superseded and not reported. Reason: Linen macro-F1 by epoch for the winning setting:

| run | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|
| final seed 1 (stopped at 9) | 0.762 | 0.962 | 0.934 | 0.773 | 0.927 | | | |
| final seed 2 (stopped at 9) | 0.906 | 0.784 | 0.886 | 0.881 | 0.880 | | | |
| search seed 0 | 0.874 | 0.891 | 0.936 | 0.941 | 0.958 | 0.948 | 0.940 | 0.942 |
| search seed 1 | 0.954 | 0.929 | 0.922 | 0.879 | 0.962 | 0.958 | 0.951 | 0.950 |

Mid-schedule, while the learning rate is still high, single-seed scores swing by up to 19 points between epochs; over epochs 10 to 12, after the learning rate has decayed, both search runs hold at 0.940 to 0.958. The original median-best-epoch rule was itself a form of selection on noisy validation: both search runs peaking at epoch 9 was partly chance. Keeping the last epoch uses no Linen information to choose the epoch, so the benchmark ensemble's Linen macro-F1 stays labelled optimistic for one reason only: the setting was chosen on Linen. The ensemble report and the 90% demo agreement check are rerun on the epoch-12 models.

**Final models, epoch 12 (Oct 27, before any test-set run; `results/classifier/ensemble.json`).** Benchmark seeds, Linen macro-F1: 0.918, 0.845, 0.886; 3-seed ensemble **0.921 (optimistic: the setting was chosen on Linen)**. Single seeds still vary by 7 points at epoch 12 (seed 1 moves between 0.78 and 0.92 over epochs 5 to 12), which is why the final models are ensembles. These are the benchmark (arms C and D) and demo models.

**The demo agreement check cannot detect a bad seed, as designed.** Each demo seed agrees with the benchmark ensemble on 91.83% of Linen predictions, all three exactly the same. The demo seeds trained on Linen and get 99.7%, 99.85% and 100% of it right, so the agreement equals the benchmark ensemble's own Linen accuracy (91.83%), whatever the demo seeds do. The check passes, but it carries no information about the demo seeds. On images neither model trained on (the 2,887 unused images: unseen defect types on non-test fabrics, no labels needed), benchmark seeds agree with the benchmark ensemble on 81 to 83% and demo seeds on 69 to 74%. Those images have no correct class among the five, so this is not a clean health signal either. A replacement check is an open decision; the demo model is not used by any benchmark arm.

**Demo model.** The winning setting is trained on all 11 fabrics (CC BY only, 7,148 images) with no validation fabric; its weights are those after the last epoch. It serves the app and the phone test set. StitchingNet test-fabric numbers come from the 8-fabric model with the same setting, scored once.

**Chosen setting** (Stage 2, Oct 27, before any test-set run; `results/classifier/stage2_selection.csv`, `selection.json`): **ConvNeXt-Tiny, strong augmentation, lr 2e-4**, 12-epoch schedule, label smoothing 0.1. Linen macro-F1 by seed, best epoch:

| setting | seed 0 | seed 1 | mean |
|---|---|---|---|
| ConvNeXt-Tiny, strong aug, lr 2e-4 | 0.958 | 0.962 | **0.960** |
| EfficientNet-B0, strong aug, lr 2e-4 | 0.894 | 0.881 | 0.888 |
| EfficientNet-B0, strong aug, lr 5e-4 | 0.881 | 0.892 | 0.886 |

Stage 1 seed-0 scores for the other settings: EfficientNet-B0 with TrivialAugment 0.877, baseline 0.862, MobileNetV3-Large with strong aug 0.832, EfficientNet-B0 with a 10x lower backbone lr 0.819. ConvNeXt-Tiny uses LayerNorm, so frozen BatchNorm does not apply to it; its lead comes from the architecture and learning rate. It is the largest candidate (28.6M parameters vs 5.3M); latency on CPU is measured at export.

## Muse Spark settings (chosen on validation, before any test-set run)

**One test image per request**, as in the product, plus the reference image(s) for few-shot. Test images are never batched, because answers could then depend on the other images and their order. Batching is used only outside evaluation.

**448 px input, `reasoning_effort: minimal`.** Chosen on CC BY Linen validation images only: 125 images, 25 per class, zero-shot, comparing default, low and minimal effort at 224 and 448 px (`scripts/val/spark_settings.py`, `results/val/`). Paired image bootstrap, 95% CIs:

- 448 px beat 224 px at every effort level on exact-class accuracy: default +0.048 (0.008 to 0.096), low +0.064 (0.016 to 0.120), minimal +0.112 (0.064 to 0.168). StitchingNet images are 224 px, so this is upsampling: the model gets more image tokens, not more detail. Phone crops are sent at 448 px from real pixels.
- At 448 px, more reasoning did not help: minimal vs default +0.016 (-0.016 to 0.048), minimal vs low +0.008 (-0.016 to 0.032), with about 4 times fewer output tokens than default and about a third of the latency (median 1.1 s vs 3.1 s).

An earlier run of this check (150 images) included 25 CC BY-NC puckering images. It was rerun on CC BY images only, before any test-set run, and gave the same choice.

The benchmark and the app use the same setting. Before the few-shot and hybrid arms run on test data, the effort level is rechecked for them on the same validation sample, because comparing against a reference may need more reasoning. Both rechecks kept minimal (below).

**Few-shot design: one good-seam reference, minimal effort** (chosen Oct 27, before any test-set run). Same 125 Linen images, 448 px, one image to inspect per request (`scripts/val/spark_fewshot.py`, `results/val/spark_fewshot_*.csv`). References come from the training fabrics only: the query's fabric when it is a training fabric, otherwise one training fabric drawn with the seed (Satin for every Linen query). Paired image bootstrap, 95% CIs:

| design | effort | macro-F1 | skipped recall | skipped called good | major recall | median s | input / output tokens |
|---|---|---|---|---|---|---|---|
| zero-shot | minimal | 0.582 | 0.00 | 0.40 | 0.707 | 1.10 | 415 / 156 |
| good reference | minimal | 0.587 | 0.00 | 0.20 | 0.787 | 0.95 | 720 / 129 |
| good + 1 per defect | minimal | 0.594 | 0.00 | 0.24 | 0.733 | 0.94 | 1,772 / 118 |
| good + 1 per defect | low | 0.610 | 0.00 | 0.28 | 0.773 | 1.73 | 1,772 / 405 |

- Major-defect recall vs zero-shot: good reference +0.080 (0.026 to 0.143); good + defects minimal +0.027 (-0.049 to 0.101); good + defects low +0.067 (0.000 to 0.137).
- Good + defects at low effort vs good reference: major recall -0.013 (-0.073 to 0.045), macro-F1 +0.022 (-0.006 to 0.054). Low vs minimal effort for good + defects: macro-F1 +0.015 (-0.019 to 0.055).
- The good reference is the only design that beats zero-shot on the headline metric with a CI above zero, and the larger designs do not beat it, at up to 1.8 times the latency and 2.5 times the input tokens. Arm B is therefore one good reference (k=1); the earlier k=3 variant is dropped.
- No design names a skipped stitch: Spark calls them crooked seams or good in every design. Defect examples halve "called good" (0.40 to 0.24) but do not teach the class. Skipped stitches are left to the classifier tool in the hybrid arm, and this is reported as a Spark failure mode.
- Limits: one validation fabric, 25 images per class, and one fixed reference set; differences of a few points are within noise.

**Puckering** cannot be checked here: there is no CC BY puckering data. The puckering prompt and its few-shot references are finalised only when the Kadiri {F}-5-3 reference pairs arrive, before the phone test pairs are scored.

**Prompt revision: rejected, the current prompt stays** (Oct 27, before any test-set run). One revision only: visual definitions for every label (`VISUAL_PROMPT` in `src/stitchcheck/spark/classify.py`). Tested with design (a) on a fresh seeded Linen sample (25 per class, no image shared with the 125 above), against a rule committed before the run: keep it only if skipped-stitch or major-defect recall improves with a CI above zero and the share of good seams flagged does not rise with a CI above zero (`scripts/val/spark_prompt_revision.py`, `results/val/spark_prompt_revision_*.csv`). Visual vs current: skipped recall +0.28 (0.11 to 0.47), major recall +0.067 (-0.013 to 0.151), macro-F1 +0.119 (0.038 to 0.194), but good seams flagged rose from 0% to 20%, +0.20 (0.05 to 0.375), so the rule rejects it. Arms A, B and D use the current prompt. No further prompt tuning.

**Hybrid design (arm D and the app)** (Oct 27, before any test-set run).
- **One function.** `inspect()` is used by benchmark arm D and by the app. One request with the image to inspect at 448 px, one good reference (design (a)), minimal effort, and a `classify_stitch` tool that returns the classifier ensemble's top-2 labels and probabilities. The Meta API accepts only `tool_choice: "auto"`, so a reply without the tool call is retried once; if Spark still skips it, the photo goes to the checker with the classifier's label. In a 10-image smoke test and the 125-image check below, Spark called the tool every time.
- **Routing.** Spark and the classifier agree: that label. They disagree, or Spark says ask_checker: ask_checker, with Spark's label as the proposal shown to the checker. Spark's stated confidence is stored, not used for routing.
- **Major-defect protection.** If the classifier's top label is skipped_stitch, broken_stitch or stain_damage with probability at least **t = 0.60**, the proposal cannot be good or crooked_seam: it stays the classifier's label and the photo goes to the checker.
- **Choosing t.** Classifier only, all 673 Linen images: the lowest t on a 0.50 to 0.90 grid (step 0.05) at which every major class's precision is at least 0.90. At 0.60: skipped 0.903 (113 calls), broken 1.00, stain 1.00; at 0.55 skipped precision is 0.896. *Caveat:* the classifier setting was chosen on Linen, so its Linen probabilities are optimistic; on the test fabrics and phone photos the same t protects more wrong calls.
- **Severity** comes from a fixed map (skipped, broken, open seam: major); Spark judges only stain_damage, crooked_seam and puckering, and an unjudged one defaults to major.
- **Sanity check vs the classifier alone**, same 125 Linen images (`scripts/val/hybrid_sanity.py`, `results/val/hybrid_sanity_*.csv`):

| system | macro-F1 | major recall | auto major recall | good flagged | sent to checker |
|---|---|---|---|---|---|
| classifier alone | 0.944 | 1.000 | 1.000 | 16% | 0% |
| hybrid, auto label | 0.766 | 1.000 | 0.627 | 16% | 25.6% |
| hybrid, label shown to checker | 0.960 | 0.973 | 0.973 | 4% | n/a |

  Spark disagrees with the classifier on a quarter of the photos, which go to the checker, so major recall holds only with checker review. The label shown to the checker cuts flagged good seams from 16% to 4%, and the protection keeps skipped stitches proposed as skipped (0.96) although Spark itself never names them. Median latency 1.9 s for the two-turn call. Effort recheck: low vs minimal changed shown-label macro-F1 by -0.004 (-0.027 to 0.020) and auto major recall by -0.04 (-0.09 to 0.00), at 3.0 s vs 1.9 s, so minimal stays.

**References on the StitchingNet test.** Test fabrics (Cotton, Oxford) are not training fabrics, so every test query gets its good reference from the seeded training fabric, Satin, not from its own fabric. Few-shot (arms B and D) is therefore understated on StitchingNet. The Kadiri phone set, with references sewn on the same fabric (`{F}-1-5`), is the fair few-shot test.

**Phone set runs the product path.** On the Kadiri photos, arms C and D use the app's SAM seam crop and tiling, the same code as the per-photo pipeline, so the phone-set numbers describe the product as built.

## SAM 3.1: garment part and seam localisation

Tested on StitchingNet swatches and on four photos of one shirt: full shot, collar, pocket and hem close-ups (`scripts/smoke/sam_seam.py`, `results/smoke/sam_seam*.json`). SAM is used only for garment parts and stitch lines, never to name defects.

- **Part suggestion.** SAM runs "collar", "pocket" and "hem" on every photo, close-up or full shot. The app pre-selects the highest-scoring part above the part threshold, and the checker confirms or changes it with one tap. If no part clears the threshold, the checker picks it. On the full shot all three parts were found (0.75 to 0.96); on the collar and pocket close-ups the right part scored 0.89 and 0.98; the hem close-up found no hem.
- **Seam localisation.** On close-ups, the prompt "seam" finds the stitch lines, and the crop tiles around them go to the classifier and Muse Spark. On the pocket close-up it traced the topstitching closely. "seam" scored higher than "stitch line" on swatches (0.81 vs 0.69).
- **Retake.** If "seam" returns nothing above the seam threshold, the app asks: "Move closer to the stitching." (The hem close-up returned nothing.)
- **Thresholds.** Part threshold: 0.7 (source: shirt photos; the one false part, a "pocket" at the edge of the hem close-up, scored 0.66). Seam threshold: to be set on the tailor's reference pairs plus the shirt photos, never on the tailor's test photos. Chosen values and their source are recorded here before the phone set is evaluated.

## Telugu voice corrections

Tested on two 10-second clips of mixed Telugu and English ("machine ఏడు, bundle ముప్పై నాలుగు, collar దగ్గర కుట్లు జారాయి, ఇది major defect"), one quiet and one with background noise (`scripts/smoke/`, `results/smoke/`). Each takes about 2.3 seconds to transcribe.

- **Always send the Telugu + English language hint and the keyword list** (`src/stitchcheck/voice.py`). Without a hint, the noisy clip came back partly in Kannada script. Without keywords, "major" was heard as "manager".
- **Keywords are English only:** machine, bundle, operator, major, minor, the defect names and the garment parts. Adding the Telugu defect words (జారాయి, తెగింది, వంకర, ముడతలు, మరక) as keywords made the defect word itself come back wrong more often: జారాయి was right in 4 of 12 runs with them, 10 of 12 without (`results/smoke/transcribe_keywords.csv`). The Telugu words go to Muse Spark instead, for the mapping step.
- **Muse Spark maps the transcript onto the fixed defect list only**, using the Telugu words as a guide (జారాయి = skipped stitch, తెగింది = broken stitch, వంకర = crooked seam, ముడతలు = puckering, మరక = stain). If the defect word is ambiguous or matches nothing, it asks the checker rather than guessing.
- **Nothing is logged from voice alone.** The checker sees the parsed entry (machine, bundle, part, defect, severity) and taps to confirm. This matters because the defect word is the least stable part of the transcript: across reruns of the same clip it sometimes changed to a different word.

## Test-set lock

Before any test-set run, everything above is committed and tagged `test-lock`: prompts (by hash), image size, effort, few-shot design and reference paths, t, the routing and severity rules, the three benchmark classifier runs, the subset file, the metrics and the bootstrap settings (`results/test/lock.json`). The test runner (`scripts/test/run_arms.py`) refuses to start if the working tree has changes or HEAD is not the tagged commit. Arms A, B, C and D run on the StitchingNet test in one go. The only change allowed after the lock is the puckering definition and its references, decided on the `{F}-5-3` reference pairs before any phone test pair is scored. Arm E, if run, uses the locked prompts and references.

## Day 5 decision rule

If hybrid recall on major defects on the phone set is below 60% (pair-bootstrap point estimate), the product switches to suggestion mode: the AI proposes, the checker confirms every photo, and the README reports it.

## Licence coverage

StitchingNet is CC BY-NC 4.0. StitchingNet-Seg republishes the same images for 8 classes under CC BY 4.0 (verified by file hash, Oct 25, `results/audit/`). Every image in train, validation and test is byte-identical to the CC BY rows of the earlier split, so the change of source did not change any set. Puckering, needle mark and bobbin thread pulling up exist only in the CC BY-NC release and are not used.
