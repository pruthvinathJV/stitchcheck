# Design notes

## Evaluation data

**Leave-2-fabrics-out.** StitchingNet has 11 fabrics, and each one uses a single thread type. The test set holds out Cotton (poly thread) and Oxford (core thread), both shirting fabrics; Linen is the validation fabric; the other 8 are for training. Validation is also by fabric because the images are sequential frames: the same physical defect often appears several times with small shifts, so a random image split would leak.

**Exact duplicates are removed before splitting** (147 images, all within one fabric and class).

**Six classes for the classifier:** good, skipped_stitch, broken_stitch, crooked_seam, puckering, stain_damage. The other five StitchingNet defect classes (pinched fabric, needle mark, thread sagging, overlapped stitch, bobbin thread pulling up) are never trained on. Those images from the held-out fabrics form an "unseen defect" test. It measures (1) how often each system calls them good, and (2) how often Muse Spark flags them as an unknown defect.

**Muse Spark subset.** The Spark arms run on a fixed, seeded sample: 42 images per class per test fabric (504), plus 10 per unseen class per test fabric (100).

**Two classifiers.** Benchmark numbers on StitchingNet come from a model trained on 8 fabrics. The demo and the phone test set use a model retrained on all 11 fabrics. Every reported number says which model produced it.

**Phone test set.** Three shirting cottons (white, dark, checks). Seven seam types: good, skipped, broken, crooked, puckering, stain, and open seam (an unseen defect). Each sewn sample is photographed at four chalk marks 15 cm apart, top-down, in daylight. Confidence intervals resample whole sewn samples, not single photos, because photos of the same sample are correlated. The phone set is never used for training or tuning.

## Licence coverage

StitchingNet is CC BY-NC 4.0. StitchingNet-Seg republishes the same images for 8 classes under CC BY 4.0 (verified by file hash). For the six classifier classes, every good, skipped, broken, crooked and stain image is covered by CC BY; only puckering relies solely on the CC BY-NC release.
