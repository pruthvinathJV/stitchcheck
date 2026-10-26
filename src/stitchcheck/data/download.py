"""Download StitchingNet (Kaggle) and StitchingNet-Seg (figshare) into data/raw/.

python -m stitchcheck.data.download

Images are never committed; this script recreates them locally.
"""
import os
import zipfile
from pathlib import Path

import requests

from stitchcheck.data.stitchingnet import SEG_DIR, STITCHINGNET_DIR

KAGGLE_DATASET = "hyungjung/stitchingnet-dataset"
SEG_FILE_URL = "https://ndownloader.figshare.com/files/67145957"  # StitchingNet-Seg.zip, doi:10.6084/m9.figshare.31222708


def download_stitchingnet():
    if STITCHINGNET_DIR.exists():
        return print(f"StitchingNet already at {STITCHINGNET_DIR}")
    os.environ.setdefault("KAGGLEHUB_CACHE", str(Path("data/raw/kagglehub").resolve()))
    import kagglehub
    path = kagglehub.dataset_download(KAGGLE_DATASET)
    print(f"StitchingNet -> {path}")


def download_seg():
    if SEG_DIR.exists():
        return print(f"StitchingNet-Seg already at {SEG_DIR}")
    zip_path = SEG_DIR.parent.parent / "StitchingNet-Seg.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(SEG_FILE_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    zipfile.ZipFile(zip_path).extractall(SEG_DIR.parent)
    print(f"StitchingNet-Seg -> {SEG_DIR}")


if __name__ == "__main__":
    download_stitchingnet()
    download_seg()
