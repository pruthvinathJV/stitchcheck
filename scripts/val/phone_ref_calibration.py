"""Seam threshold and stitch scale on the Kadiri good reference pairs ({F}-1-5), before any test pair is scored.

python scripts/val/phone_ref_calibration.py

Per reference photo: every SAM "seam" object with its confidence, the stitch pitch found on probe tiles along the best
one (if any), and an overlay of the best mask in data/cache/phone_ref_cal/ (never in git). Summary without images in
results/val/phone_ref_calibration.json. Reference pairs only: data/phone/ref/.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from stitchcheck.backend.pipeline import PROBE_FRAC, SEAM_PROMPT, clean_photo, seam_overlay, seam_tiles
from stitchcheck.backend.stitch import stitch_pitch
from stitchcheck.meta_api import sam_segment

REF = Path("data/phone/ref")
OUT = Path("data/cache/phone_ref_cal")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in pd.read_csv(REF / "manifest.csv").itertuples():
        im, meta = clean_photo((REF / "images" / f"{r.photo_id}.jpg").read_bytes())
        objs = [o for o in sam_segment(im, SEAM_PROMPT)[0] if "mask" in o]
        confs = sorted((round(o.get("confidence", 0), 3) for o in objs), reverse=True)
        best = max(objs, key=lambda o: o.get("confidence", 0), default=None)
        pitches = []
        if best is not None:
            seam_overlay(im, best["mask"]).save(OUT / f"{r.photo_id}.jpg")
            probes = seam_tiles(im, best["mask"], side=PROBE_FRAC * min(im.size))
            for i, p in enumerate(probes):
                p.save(OUT / f"{r.photo_id}_probe{i}.jpg")
            pitches = [round(x["pitch"], 1) for x in (stitch_pitch(np.array(p)) for p in probes) if x["found"]]
        rows.append(dict(photo_id=r.photo_id, size=f"{im.width}x{im.height}", focal_35mm=meta["focal_35mm"],
                         seam_confidences=confs, pitches=pitches))
        print(rows[-1], flush=True)
    out = Path("results/val/phone_ref_calibration.json")
    out.write_text(json.dumps(rows, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
