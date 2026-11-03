"""Seam strips for the skipped-stitch review (docs/design.md, Phone-set scoring rules), before any scoring.

python scripts/val/phone_review_strips.py

For every check_visible photo: SAM's best "seam" mask, and the full length of the seam cut out, laid horizontal and
split into rows, at full resolution. No classifier is run, so the review does not depend on any arm. Images go to
data/cache/review/, never to git.
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from stitchcheck.backend.pipeline import SEAM_PROMPT, clean_photo
from stitchcheck.meta_api import sam_segment

PHONE = Path("data/phone")
OUT = Path("data/cache/review")
ROW = 520  # px of seam per row


def strip(im, mask, height=120):
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], 1).astype(float)
    mean = pts.mean(0)
    u = np.linalg.svd(pts - mean, full_matrices=False)[2][0]
    angle = math.degrees(math.atan2(u[1], u[0]))
    t = (pts - mean) @ u
    pad = max(im.size)  # room so the rotated seam is never clipped by the photo's own edges
    canvas = Image.new("RGB", (im.width + 2 * pad, im.height + 2 * pad), "white")
    canvas.paste(im, (pad, pad))
    mean = mean + pad
    big = canvas.rotate(angle, center=tuple(mean), resample=Image.BICUBIC)  # seam along x through the mean point
    s = big.crop((int(mean[0] + t.min()), int(mean[1] - height / 2), int(mean[0] + t.max()), int(mean[1] + height / 2)))
    rows = [s.crop((x, 0, min(x + ROW, s.width), height)) for x in range(0, s.width, ROW)]
    sheet = Image.new("RGB", (ROW * 3, height * 3 * len(rows)), "white")
    for i, r in enumerate(rows):
        sheet.paste(r.resize((r.width * 3, height * 3)), (0, i * height * 3))
    return sheet


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    man = pd.read_csv(PHONE / "manifest.csv")
    for r in man[man["check_visible"] == 1].itertuples():
        im, _ = clean_photo((PHONE / "images" / f"{r.photo_id}.jpg").read_bytes())
        objs = [o for o in sam_segment(im, SEAM_PROMPT)[0] if "mask" in o]
        if not objs:
            print(r.photo_id, "no seam found")
            continue
        best = max(objs, key=lambda o: o.get("confidence", 0))
        strip(im, best["mask"]).save(OUT / f"{r.photo_id}_strip.jpg", quality=92)
        print(r.photo_id, round(best.get("confidence", 0), 3), flush=True)


if __name__ == "__main__":
    main()
