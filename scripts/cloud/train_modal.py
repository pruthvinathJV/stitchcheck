"""Train classifiers on a Modal cloud GPU (one GPU type for every run) and copy the results back here.

modal run scripts/cloud/train_modal.py --jobs JOBS.txt         one trainer command per line (args after
                                                               `python -m stitchcheck.classifier.train`)
modal run scripts/cloud/train_modal.py --ensemble "ARGS"       args for scripts/train/ensemble.py
modal run scripts/cloud/train_modal.py --fetch-only            copy finished runs back without training

Images: StitchingNet-Seg (CC BY 4.0), downloaded once from figshare into a Modal volume and checked file by file
against data/manifests/stitchingnet_seg.csv (md5). Weights go to models/<run>/ and metrics to
results/classifier/<run>/, the same layout as a local run. Runs never resume from a checkpoint: each one
trains from scratch in a single process.
"""
import hashlib
import os
import runpy
import sys
import zipfile
from pathlib import Path

import modal

GPU = "A10G"
SEG_URL = "https://ndownloader.figshare.com/files/67145957"  # StitchingNet-Seg.zip, doi:10.6084/m9.figshare.31222708
FILES = {"data/splits/stitchingnet_v2.csv": "/files/stitchingnet_v2.csv",
         "data/manifests/stitchingnet_seg.csv": "/files/stitchingnet_seg.csv",
         "scripts/train/ensemble.py": "/files/ensemble.py"}

app = modal.App("stitchcheck-train")
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch==2.14.1", "torchvision==0.29.1", "pandas==3.1.0", "pillow==13.0.0", "numpy", "requests")
for local, remote in FILES.items():
    image = image.add_local_file(local, remote)
image = image.add_local_python_source("stitchcheck")
data = modal.Volume.from_name("stitchcheck-data", create_if_missing=True)
runs = modal.Volume.from_name("stitchcheck-runs", create_if_missing=True)
SEG_ROOT = Path("/data/stitchingnet_seg")


def _workdir():
    """The trainer uses repo-relative paths; recreate them in /work, pointing at the volumes."""
    work = Path("/work")
    for d in ("data/raw", "data/splits", "data/manifests"):
        (work / d).mkdir(parents=True, exist_ok=True)
    links = {work / "data/raw/stitchingnet_seg": SEG_ROOT, work / "models": Path("/runs/models"),
             work / "results": Path("/runs/results")}
    for link, target in links.items():
        target.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(target)
    for local, remote in FILES.items():
        dst = work / local
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(Path(remote).read_bytes())
    os.chdir(work)


@app.function(image=image, volumes={"/data": data}, cpu=4, timeout=3600)
def prepare():
    import pandas as pd
    import requests
    dataset = SEG_ROOT / "extracted/dataset"
    if not dataset.exists():
        zip_path = Path("/tmp/StitchingNet-Seg.zip")
        with requests.get(SEG_URL, stream=True, timeout=60) as r:
            r.raise_for_status()
            with zip_path.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        zipfile.ZipFile(zip_path).extractall(SEG_ROOT / "extracted")
        data.commit()
    manifest = pd.read_csv("/files/stitchingnet_seg.csv")
    bad = [p for p, m in zip(manifest["path"], manifest["md5"])
           if not (dataset / p).exists() or hashlib.md5((dataset / p).read_bytes()).hexdigest() != m]
    assert not bad, f"{len(bad)} images missing or different from the manifest, e.g. {bad[:3]}"
    return len(manifest)


@app.function(image=image, gpu=GPU, cpu=8, memory=16384, volumes={"/data": data, "/runs": runs}, timeout=7200)
def train(argv):
    import torch
    _workdir()
    assert "--resume" not in argv
    sys.argv = ["train"] + argv
    print("GPU:", torch.cuda.get_device_name(0), "| args:", " ".join(argv), flush=True)
    try:
        runpy.run_module("stitchcheck.classifier.train", run_name="__main__")
    finally:
        runs.commit()
    return " ".join(argv)


@app.function(image=image, gpu=GPU, cpu=8, memory=16384, volumes={"/data": data, "/runs": runs}, timeout=3600)
def ensemble(argv):
    _workdir()
    runs.reload()
    sys.argv = ["ensemble"] + argv
    try:
        runpy.run_path("/files/ensemble.py", run_name="__main__")
    finally:
        runs.commit()


def fetch(prefixes=("models", "results")):
    """Copy run outputs from the volume into the repo (models/ is gitignored)."""
    n = 0
    for prefix in prefixes:
        for entry in runs.listdir(prefix, recursive=True):
            if entry.type != modal.volume.FileEntryType.FILE:
                continue
            dst = Path(entry.path)
            # last.pt only holds resume state (runs never resume); weights already here are not copied again
            if dst.name == "last.pt" or (prefix == "models" and dst.exists() and dst.stat().st_size == entry.size):
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            with dst.open("wb") as f:
                for chunk in runs.read_file(entry.path):
                    f.write(chunk)
            n += 1
    print(f"fetched {n} files into models/ and results/")


@app.local_entrypoint()
def main(jobs: str = "", ensemble_args: str = "", fetch_only: bool = False):
    if not fetch_only:
        print("images checked against the manifest:", prepare.remote())
    if jobs:
        argvs = [line.split() for line in Path(jobs).read_text().splitlines() if line.strip() and not line.startswith("#")]
        for done in train.map(argvs, return_exceptions=True):
            print("finished:" if isinstance(done, str) else "FAILED:", done)
    if ensemble_args:
        ensemble.remote(ensemble_args.split())
    fetch()
