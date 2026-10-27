# StitchCheck

A phone app that turns any worker into a reliable stitch inspector, so a new 25-machine garment unit in rural Andhra Pradesh can prove factory-grade quality and win orders.

A checker photographs a seam close-up. SAM 3.1 finds the seam, a classifier fine-tuned on StitchingNet-Seg flags likely defects, and Muse Spark acts as the inspector's brain: it calls the classifier as a tool, compares the seam with a known-good reference, confirms or overrides the call, assigns AQL severity and explains it in one line. When Spark and the classifier disagree, the checker decides, with one tap or a Telugu voice correction. A Muse Spark agent logs every defect by operator, machine and bundle, tracks DHU live and drafts the daily QC report and AQL 2.5 summary.

> Status: in progress (Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026).

- Demo video: TODO
- Live app: TODO

## Built during the hackathon

Before Oct 26: research and planning documents only. All code was written during the hackathon; the commit history shows when.

- [x] Dataset download, audit (counts, duplicates, licence coverage) and leave-2-fabrics-out split
- [x] Phone test set ingest (WhatsApp export to labelled manifest) and photo review page
- [x] Meta Model API clients and smoke tests (Muse Spark images per request, SAM 3.1 seam masks, Telugu transcription)
- [x] Stitch classifier fine-tuned on StitchingNet-Seg (ConvNeXt-Tiny, 3-seed ensemble, trained on Modal)
- [x] Muse Spark zero-shot and few-shot evaluation
- [ ] SAM 3.1 seam localisation and tiling
- [x] Hybrid inspector (Muse Spark with the classifier as a tool) and the locked StitchingNet benchmark
- [ ] Telugu voice correction
- [ ] QC agent: defect log, DHU, AQL 2.5 summary, daily report
- [ ] Phone app and dashboard

## Meta models used

| Model | Where |
|---|---|
| Muse Spark (`muse-spark-1.3`) | TODO |
| SAM 3.1 | TODO |
| Muse Voice Transcribe | TODO |
| Muse Code | TODO |

## Architecture

TODO

## Results

Every number here traces to a script and a file under `results/`. Settings were chosen on a validation fabric (Linen) and locked before any test run (`docs/design.md`, tag `test-lock-2`).

### StitchingNet test (held-out fabrics: Cotton and Oxford)

Headline metric: recall on major defects (skipped stitch, broken stitch, stain), the defects that fail a buyer audit. 420 test images (84 per class) plus 60 images of defect types no model was trained on. 95% intervals in brackets.

| System | Major recall | Good seams flagged | Macro-F1 | Sent to checker |
|---|---|---|---|---|
| **StitchCheck: Muse Spark + classifier, checker in the loop** | **0.929** [0.897, 0.959] | 9.5% | n/a | 21.7% |
| StitchCheck, label shown to the checker | 0.893 [0.855, 0.929] | 1.2% | 0.891 | n/a |
| Classifier alone (ConvNeXt-Tiny, 3 seeds) | 0.921 [0.887, 0.952] | 8.3% | 0.887 | 0% |
| Muse Spark with a good-seam reference | 0.595 [0.533, 0.657] | 0% | 0.618 | 0% |
| Muse Spark zero-shot | 0.532 [0.470, 0.596] | 0% | 0.579 | 0% |

- **Sent to checker:** the photos where Muse Spark and the classifier disagree, which the app marks for the checker to decide instead of logging a label itself.
- **Label shown to the checker:** the label the app pre-fills on every photo, including the ones sent to the checker; the checker agrees with one tap or changes it.

With the checker deciding the 21.7% of photos where the two disagree, StitchCheck catches 92.9% of major defects (classifier alone 92.1%; difference +0.8 points [0.0, 2.0]). If the checker simply accepts the pre-filled label, false alarms on good seams drop from 8.3% to 1.2%, at a cost of 2.8 points of major recall [0.8, 4.9]. The hybrid takes a median 1.7 s per photo and costs about $0.49 per 100 photos.

Muse Spark on its own misses about half of the major defects: it almost never names a skipped stitch (0 to 2% recall) and passes about half the stains as good. It never flags a good seam, so its errors all go one way. A good-seam reference helps (+6.3 points of major recall [2.4, 10.4]). On StitchingNet the reference comes from a training fabric, not the test fabric, so few-shot is understated there; the phone set uses references sewn on the same fabric.

**Muse Spark never flagged "unknown defect" on StitchingNet**, in any arm, on 60 images of defect types the classifier was never trained on. In the hybrid those images are caught as disagreements (5% called good), not as new defects. The open-seam pairs in the phone test set are the second test of this.

Full tables: `results/test/summary.csv`, `per_class.csv`, `paired.csv` (`scripts/test/analyse.py`). StitchingNet frames of the same defect are correlated, so these intervals are on the narrow side.

### Known limitations and next improvements

- **Skipped stitches can leak through the label shown to the checker.** The classifier's skipped-stitch calls are protected from being relabelled good or crooked only when its probability is at least 0.60. Below that, Muse Spark's "good" or "crooked seam" is pre-filled: skipped-stitch recall of the shown label is 0.667 against the classifier's 0.702. The 0.60 threshold and the rule were fixed on validation data and are not changed after seeing test results; a lower threshold is the next improvement to test on new validation data.
- **Muse Spark does not name new defect types** on StitchingNet (above).
- **Phone photos are the real test.** StitchingNet is lab swatches from a fixed camera; results on phone photos of real shirts follow when the phone set is scored.

## Data and licences

The StitchingNet authors declined use beyond the non-commercial license, so the classifier is trained only on CC BY data and covers four defects. Puckering is detected by Muse Spark using reference seams we sewed ourselves.

| Dataset | Licence | Use |
|---|---|---|
| [StitchingNet-Seg](https://doi.org/10.6084/m9.figshare.31222708) (Park, Jung and Kim, 2026) | CC BY 4.0 | The only image source for training and evaluation: good seams, four defect classes (skipped, broken, crooked, stain) and three unseen defect classes |
| Phone test set (ours) | Not redistributed | Real out-of-distribution test, and the only puckering test: shirting cottons sewn with deliberate defects, photographed on a phone |

The CC BY-NC release of [StitchingNet](https://www.kaggle.com/datasets/hyungjung/stitchingnet-dataset) (Kim et al.) was used once, before training, to check licence coverage (`results/audit/`). No model is trained or evaluated on it, and every split row is checked to be CC BY when it is loaded.

Dataset images and phone photos are not redistributed in this repository. The repo contains only download scripts, manifests (file names and hashes), split files, labels and metrics. `data/manifests/stitchingnet_seg.csv` lists every image used.

## How to run

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[data,api,dev]'
cp .env.example .env                        # add MODEL_API_KEY

.venv/bin/python -m stitchcheck.data.download   # StitchingNet-Seg (CC BY) into data/raw/
.venv/bin/python -m stitchcheck.data.manifest   # data/manifests/stitchingnet_seg.csv
.venv/bin/python -m stitchcheck.data.splits     # data/splits/stitchingnet_v2.csv

# Phone test set
.venv/bin/python -m stitchcheck.phone.ingest path/to/whatsapp_export.zip
.venv/bin/python -m stitchcheck.phone.review    # http://127.0.0.1:8765
.venv/bin/python -m stitchcheck.phone.review --export

# API smoke tests
.venv/bin/python scripts/smoke/spark_images.py --big
.venv/bin/python scripts/smoke/sam_seam.py
.venv/bin/python scripts/smoke/transcribe_telugu.py clip.m4a

.venv/bin/python -m pytest -q
```

On macOS, if the repo lives under ~/Desktop or ~/Documents, files there can get the hidden flag and Python 3.13+ then skips the editable install's `.pth` file. Create the venv elsewhere and symlink it: `python3 -m venv ~/.venvs/stitchcheck && ln -s ~/.venvs/stitchcheck .venv`.

## Licence

Code: MIT. Datasets keep their own licences (see above).
