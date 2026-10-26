# Design notes

## Evaluation data

**Leave-2-fabrics-out.** StitchingNet has 11 fabrics, and each one uses a single thread type. The test set holds out Cotton (poly thread) and Oxford (core thread), both shirting fabrics; Linen is the validation fabric; the other 8 are for training. Validation is also by fabric because the images are sequential frames: the same physical defect often appears several times with small shifts, so a random image split would leak.

**Exact duplicates are removed before splitting** (147 images, all within one fabric and class).

**Six classes for the classifier:** good, skipped_stitch, broken_stitch, crooked_seam, puckering, stain_damage. The other five StitchingNet defect classes (pinched fabric, needle mark, thread sagging, overlapped stitch, bobbin thread pulling up) are never trained on. Those images from the held-out fabrics form an "unseen defect" test. It measures (1) how often each system calls them good, and (2) how often Muse Spark flags them as an unknown defect.

**Muse Spark subset.** The Spark arms run on a fixed, seeded sample: 42 images per class per test fabric (504), plus 10 per unseen class per test fabric (100).

**Two classifiers.** Benchmark numbers on StitchingNet come from a model trained on 8 fabrics. The demo and the phone test set use a model retrained on all 11 fabrics. Every reported number says which model produced it.

**Phone test set.** Three shirting cottons (white, dark, checks). Seven seam types: good, skipped, broken, crooked, puckering, stain, and open seam (an unseen defect). Per fabric: 4 good pairs, 3 pairs each of skipped and broken stitch, and 2 pairs of every other type (51 pairs, 204 photos in all). Each sewn pair is photographed at four chalk marks 15 cm apart, top-down, in daylight. Broken stitch, crooked seam, stain and open seam are made at every chalk mark. Skipped stitch and puckering run along the whole seam, so a mark may show no visible defect; those photos are reviewed one by one and relabelled as good or rejected. All phone-set confidence intervals come from a bootstrap that resamples whole pairs, not single photos, because photos of the same pair are correlated. The phone set is never used for training or tuning.

**Puckering fallback.** If the CC BY-NC terms rule out using StitchingNet puckering, the classifier drops it and puckering is evaluated with zero-shot and few-shot Muse Spark only. The README will say so.

## Licence coverage

StitchingNet is CC BY-NC 4.0. StitchingNet-Seg republishes the same images for 8 classes under CC BY 4.0 (verified by file hash). For the six classifier classes, every good, skipped, broken, crooked and stain image is covered by CC BY; only puckering relies solely on the CC BY-NC release.
