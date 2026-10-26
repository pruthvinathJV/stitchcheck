"""Check what SAM 3.1 returns for garment-part and seam concepts.

python scripts/smoke/sam_seam.py [IMAGE ...] [--prompts seam "stitch line" collar pocket hem]

With no images, uses 3 held-out StitchingNet test images. Writes results/smoke/sam_seam.json and
overlay PNGs (gitignored) to results/smoke/sam/.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from stitchcheck.data.stitchingnet import STITCHINGNET_DIR
from stitchcheck.meta_api import SAM_MODEL, sam_segment

OUT = Path("results/smoke")


def overlay(im, objects, path):
    arr = np.asarray(im.convert("RGB")).astype(float)
    for o in objects:
        if "mask" in o:
            arr[o["mask"]] = arr[o["mask"]] * 0.45 + np.array([255, 0, 160]) * 0.55
    Image.fromarray(arr.astype(np.uint8)).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--prompts", nargs="+", default=["seam", "stitch line", "collar", "pocket", "hem"])
    args = ap.parse_args()
    images = args.images
    if not images:
        test = pd.read_csv("data/splits/stitchingnet_v1.csv").query("split == 'test' and label == 'good'")
        images = [STITCHINGNET_DIR / p for p in test.sample(3, random_state=0)["path"]]
    (OUT / "sam").mkdir(parents=True, exist_ok=True)
    rows = []
    for img_path in images:
        im = Image.open(img_path).convert("RGB")
        for prompt in args.prompts:
            t0 = time.time()
            try:
                objects, raw = sam_segment(im, prompt)
                row = dict(ok=True, n_objects=len(objects),
                           confidences=[o.get("confidence") for o in objects],
                           mask_area_frac=[round(float(o["mask"].mean()), 4) for o in objects if "mask" in o],
                           raw_head=raw[:200])
                name = f"{Path(img_path).stem}_{prompt.replace(' ', '_')}.png"
                overlay(im, objects, OUT / "sam" / name)
            except Exception as e:
                row = dict(ok=False, error=f"{type(e).__name__}: {e}"[:500])
            rows.append(dict(image=Path(img_path).name, size=list(im.size), prompt=prompt,
                             seconds=round(time.time() - t0, 2), **row))
            print({k: v for k, v in rows[-1].items() if k != "raw_head"})
    (OUT / "sam_seam.json").write_text(json.dumps(dict(model=SAM_MODEL, date=time.strftime("%Y-%m-%d"), runs=rows), indent=2))


if __name__ == "__main__":
    main()
