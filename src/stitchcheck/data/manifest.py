"""Manifest of StitchingNet-Seg (figshare, CC BY 4.0): the only image source for training and evaluation.

python -m stitchcheck.data.manifest   (needs data/raw/stitchingnet_seg from stitchcheck.data.download)

Writes data/manifests/stitchingnet_seg.csv: one row per image, no pixels.
"""
from pathlib import Path

from stitchcheck.data.stitchingnet import CC_BY, SEG_DIR, scan

OUT = Path("data/manifests/stitchingnet_seg.csv")


def main():
    df = scan(SEG_DIR)
    df["licence"] = CC_BY
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"{len(df)} images, {df['md5'].nunique()} unique -> {OUT}")
    print(df["source_class"].value_counts().to_string())


if __name__ == "__main__":
    main()
