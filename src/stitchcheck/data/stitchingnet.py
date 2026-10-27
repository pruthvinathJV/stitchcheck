"""StitchingNet and StitchingNet-Seg: folder scanning, class map, licence tagging.

Both releases use the same layout: <root>/<L>. <Fabric>-<Thread>/<N>. <Class>/<file>.jpg
StitchingNet is CC BY-NC 4.0. StitchingNet-Seg (a filtered subset of the same images) is CC BY 4.0.
Every image used for training or evaluation is read from the Seg download (SEG_DIR) through load_split(),
which asserts that every row is CC BY. The NC release (STITCHINGNET_DIR) is read only by the licence audit.
"""
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

STITCHINGNET_DIR = Path("data/raw/kagglehub/datasets/hyungjung/stitchingnet-dataset/versions/6")
SEG_DIR = Path("data/raw/stitchingnet_seg/extracted/dataset")

SPLIT_FILE = Path("data/splits/stitchingnet_v2.csv")
CC_BY = "CC-BY-4.0"

# Locked 5-class map (all in the CC BY release). Other Seg defect classes are "unseen" defects:
# never used for classifier training, only as a probe for Spark's unknown_defect flag.
LABELS = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage"]
SOURCE_TO_LABEL = {
    "Normal": "good",
    "Skipped stitch": "skipped_stitch",
    "Broken stitch": "broken_stitch",
    "Crooked seam": "crooked_seam",
    "Stain and damage": "stain_damage",
}

_PREFIX = re.compile(r"^\w+\.\s*")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _name(folder):
    return _PREFIX.sub("", folder.name)


def dhash64(img):
    """64-bit difference hash, used only to look for near-duplicates."""
    g = np.asarray(img.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def scan(root, with_pixels=True):
    """One row per image: path (relative to root), fabric, thread, source class, label, md5 (+ size, dhash)."""
    root = Path(root)
    rows = []
    for fabric_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        fabric, thread = _name(fabric_dir).rsplit("-", 1)
        for class_dir in sorted(p for p in fabric_dir.iterdir() if p.is_dir()):
            source_class = _name(class_dir)
            image_dir = class_dir / "image" if (class_dir / "image").is_dir() else class_dir  # Seg layout
            for f in sorted(image_dir.iterdir()):
                if f.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                row = dict(path=f.relative_to(root).as_posix(), fabric=fabric, thread=thread,
                           source_class=source_class, label=SOURCE_TO_LABEL.get(source_class, ""),
                           md5=hashlib.md5(f.read_bytes()).hexdigest())
                if with_pixels:
                    with Image.open(f) as im:
                        row.update(width=im.width, height=im.height, mode=im.mode, dhash=dhash64(im))
                rows.append(row)
    return pd.DataFrame(rows)


def tag_licence(df, seg_root):
    """Add `licence`: CC-BY-4.0 if the exact image bytes are in StitchingNet-Seg, else CC-BY-NC-4.0 only."""
    seg_md5 = set(scan(seg_root, with_pixels=False)["md5"]) if Path(seg_root).exists() else set()
    df = df.copy()
    df["licence"] = np.where(df["md5"].isin(seg_md5), "CC-BY-4.0", "CC-BY-NC-4.0")
    return df


def load_split(path=SPLIT_FILE):
    """The split file, after checking that every image is CC BY and present in the Seg download."""
    df = pd.read_csv(path).fillna({"label": ""})
    bad = df[df["licence"] != CC_BY]
    assert bad.empty, f"{len(bad)} non-CC BY rows in {path}, e.g. {bad['path'].iloc[0]}"
    return df


def image_path(rel):
    return SEG_DIR / rel
