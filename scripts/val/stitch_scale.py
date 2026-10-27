"""Stitches per tile in StitchingNet training images: the target for cutting phone tiles.

python scripts/val/stitch_scale.py [--per-fabric 30]

Good seams from the 8 training fabrics only. Writes results/val/stitch_scale.json with the per-fabric pitch,
how often a pitch was found, and the overall stitches per 224 px tile (224 / median pitch over found estimates).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from stitchcheck.backend.stitch import MIN_PITCH, MIN_STRENGTH, stitch_pitch
from stitchcheck.data.stitchingnet import image_path, load_split


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-fabric", type=int, default=30)
    args = ap.parse_args()
    tr = load_split().query("split == 'train' and label == 'good'")
    sample = tr.groupby("fabric", group_keys=False).sample(args.per_fabric, random_state=0)
    found, per_fabric = [], {}
    for fabric, g in sample.groupby("fabric"):
        res = [stitch_pitch(np.array(Image.open(image_path(p)).convert("RGB"))) for p in g["path"]]
        ok = [r["pitch"] for r in res if r["found"]]
        found += ok
        per_fabric[fabric] = dict(n=len(res), found=len(ok), median_pitch=float(np.median(ok)) if ok else None)
    pitch = float(np.median(found))
    out = dict(images=f"{args.per_fabric} good training seams per training fabric, seed 0", tile_px=224,
               min_strength=MIN_STRENGTH, min_pitch=MIN_PITCH, n_found=len(found), median_pitch=pitch,
               pitch_iqr=[float(x) for x in np.percentile(found, [25, 75])],
               stitches_per_tile=round(224 / pitch, 2), per_fabric=per_fabric)
    Path("results/val/stitch_scale.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
