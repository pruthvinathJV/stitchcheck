# StitchCheck

A phone app that turns any worker into a reliable stitch inspector, so a new 25-machine garment unit in rural Andhra Pradesh can prove factory-grade quality and win orders.

A checker photographs a seam close-up. SAM 3.1 finds the seam, a classifier fine-tuned on StitchingNet flags likely defects, and Muse Spark acts as the inspector's brain: it calls the classifier as a tool, compares the seam with a known-good reference, confirms or overrides the call, assigns AQL severity and explains it in one line. When confidence is low, the checker decides, with one tap or a Telugu voice correction. A Muse Spark agent logs every defect by operator, machine and bundle, tracks DHU live and drafts the daily QC report and AQL 2.5 summary.

> Status: in progress (Meta Global AI Developer Hackathon, Oct 26 to Nov 4, 2026).

- Demo video: TODO
- Live app: TODO

## Built during the hackathon

Before Oct 26: research and planning documents only. All code was written during the hackathon; the commit history shows when.

- [x] Dataset download, audit (counts, duplicates, licence coverage) and leave-2-fabrics-out split
- [x] Phone test set ingest (WhatsApp export to labelled manifest) and photo review page
- [x] Meta Model API clients and smoke tests (Muse Spark images per request, SAM 3.1 seam masks, Telugu transcription)
- [ ] Stitch classifier fine-tuned on StitchingNet
- [ ] Muse Spark zero-shot and few-shot evaluation
- [ ] SAM 3.1 seam localisation and tiling
- [ ] Hybrid pipeline (Muse Spark with the classifier as a tool)
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

TODO. Every number here will trace to a script and a file under `results/`.

## Data and licences

| Dataset | Licence | Use |
|---|---|---|
| [StitchingNet](https://www.kaggle.com/datasets/hyungjung/stitchingnet-dataset) (Kim et al.) | CC BY-NC 4.0 | Training and held-out-fabric test |
| [StitchingNet-Seg](https://doi.org/10.6084/m9.figshare.31222708) (Park, Jung and Kim, 2026) | CC BY 4.0 | Same images for 8 classes; used to track which training images are available under CC BY |
| Phone test set (ours) | Not redistributed | Real out-of-distribution test: shirting cottons sewn with deliberate defects, photographed on a phone |

Dataset images and phone photos are not redistributed in this repository. The repo contains only download scripts, manifests (file names and hashes), split files, labels and metrics. `data/manifests/stitchingnet.csv` records the licence of every image.

## How to run

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[data,api,dev]'
cp .env.example .env                        # add MODEL_API_KEY

.venv/bin/python -m stitchcheck.data.download   # StitchingNet + StitchingNet-Seg into data/raw/
.venv/bin/python -m stitchcheck.data.audit      # results/audit/
.venv/bin/python -m stitchcheck.data.splits     # data/splits/stitchingnet_v1.csv

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
