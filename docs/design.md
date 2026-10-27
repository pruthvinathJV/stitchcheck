# Design notes

## Evaluation data

**Leave-2-fabrics-out.** StitchingNet has 11 fabrics, and each one uses a single thread type. The test set holds out Cotton (poly thread) and Oxford (core thread), both shirting fabrics; Linen is the validation fabric; the other 8 are for training. Validation is also by fabric because the images are sequential frames: the same physical defect often appears several times with small shifts, so a random image split would leak.

**Exact duplicates are removed before splitting** (147 images, all within one fabric and class).

**Classifier classes.** The clean classifier has five: good, skipped_stitch, broken_stitch, crooked_seam, stain_damage. The full classifier adds puckering (see "Puckering and licensing"). The other five StitchingNet defect classes (pinched fabric, needle mark, thread sagging, overlapped stitch, bobbin thread pulling up) are never trained on. Those images from the held-out fabrics form an "unseen defect" test. It measures (1) how often each system calls them good, and (2) how often Muse Spark flags them as an unknown defect.

**Muse Spark subset.** The Spark arms run on a fixed, seeded sample: 42 images per class per test fabric (504), plus 10 per unseen class per test fabric (100).

**Training fabrics.** Benchmark numbers on StitchingNet come from models trained on 8 fabrics. The demo and the phone test set use models retrained on all 11 fabrics. Every reported number says which model produced it (clean or full, 8 or 11 fabrics).

**Optional: leave-one-fabric-out.** If Day 2 has time, 11 extra runs (train on 10 fabrics, test on the 11th) give a per-fabric difficulty table. It does not change the main split.

**Phone test set.** Sewn and photographed by a tailor in Kadiri, Andhra Pradesh, on locally bought shirting cotton. Three shirting cottons (white, dark, checks). Seven seam types: good, skipped, broken, crooked, puckering, stain, and open seam (an unseen defect). Per fabric: 4 good pairs, 3 pairs each of skipped and broken stitch, and 2 pairs of every other type (51 pairs, 204 photos in all). Each sewn pair is photographed at four chalk marks 15 cm apart, top-down, in daylight. Broken stitch, crooked seam, stain and open seam are made at every chalk mark. Skipped stitch and puckering run along the whole seam, so a mark may show no visible defect; those photos are reviewed one by one and relabelled as good or rejected. All phone-set confidence intervals come from a bootstrap that resamples whole pairs, not single photos, because photos of the same pair are correlated. The phone set is never used for training or tuning.

## Puckering and licensing (fixed before training)

Two classifiers are trained:

- **Clean (default):** five classes (good, skipped_stitch, broken_stitch, crooked_seam, stain_damage), trained only on images that StitchingNet-Seg publishes under CC BY 4.0. Selected by the `licence` column of the split file.
- **Full:** six classes, adding puckering from the CC BY-NC 4.0 release. It is trained and evaluated but not shipped.

In the product, puckering is handled by Muse Spark: few-shot, with a good-seam reference from the same fabric. The QC agent still logs it, assigns severity and reports it like any other defect.

**Switch rule.** If the StitchingNet authors grant permission by Day 5 (Fri Oct 30), the demo and final results switch to the full classifier. Otherwise the clean classifier is final. The README records which one shipped. Without permission it says: "The classifier covers four defects using CC BY data only. Puckering is detected by Muse Spark."

**Evaluation.** Puckering results are reported for Muse Spark alone, on both the StitchingNet test subset and the Kadiri phone set. The full classifier's puckering numbers are a side comparison, labelled as such. The tailor's puckering pairs stay in the phone set: they are the only real-world test of Muse Spark on puckering.

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
- cost per 100 garments, assuming 5 close-ups per shirt (collar, two side seams, hem, pocket); the assumption is stated next to the number

## Systems compared

- **(A)** Zero-shot Muse Spark.
- **(B)** Few-shot Muse Spark with k=1 and k=3 good-seam references from the same fabric.
- **(C)** Fine-tuned classifier (clean; full as a side comparison).
- **(D)** Hybrid: Muse Spark calling the classifier as a tool, and judging puckering itself few-shot.
- **(E, optional)** Hybrid with Muse Spark Open, if Baseten credits arrive.

## Muse Spark settings (chosen on validation, before any test-set run)

**One test image per request**, as in the product, plus the reference image(s) for few-shot. Test images are never batched, because answers could then depend on the other images and their order. Batching is used only outside evaluation.

**448 px input, `reasoning_effort: minimal`.** Chosen on 150 Linen validation images (25 per class), zero-shot, comparing default, low and minimal effort at 224 and 448 px (`scripts/val/spark_settings.py`, `results/val/`):

- 448 px beat 224 px at every effort level, by 5 to 9 points of exact-class accuracy (paired image bootstrap CIs exclude zero). StitchingNet images are 224 px, so this is upsampling: the model gets more image tokens, not more detail. Phone crops are sent at 448 px from real pixels.
- At 448 px, more reasoning did not help: minimal effort matched default (difference +0.000, CI -0.027 to +0.027) with about 4 times fewer output tokens and less than half the latency (median 1.2 s vs 3.2 s).

The benchmark and the app use the same setting. Before the few-shot and hybrid arms run on test data, the effort level is rechecked for them on the same validation sample, because comparing against a reference may need more reasoning.

## Day 5 decision rule

If hybrid recall on major defects on the phone set is below 60% (pair-bootstrap point estimate), the product switches to suggestion mode: the AI proposes, the checker confirms every photo, and the README reports it.

## Licence coverage

StitchingNet is CC BY-NC 4.0. StitchingNet-Seg republishes the same images for 8 classes under CC BY 4.0 (verified by file hash). Every good, skipped, broken, crooked and stain image is covered by CC BY, so the clean classifier needs nothing from the CC BY-NC release. Only puckering relies solely on it.
