"""The per-photo pipeline (docs/backend_spec.md, section 3). The phone-set evaluation runs the same code.

photo -> clean copy (EXIF rotation applied, metadata dropped) -> SAM 3.1 "seam" plus the tested parts, in parallel
-> tiles along the best seam, rotated so the seam runs horizontally and scaled so the seam is as thick, relative
to the tile, as in StitchingNet -> classifier ensemble on
every tile -> the tile most likely to be a defect goes to inspect() with the classifier's top 2 and a good reference
-> part mapped to the line plan -> pending inspection row, with the agent run logged.
"""
import hashlib
import io
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from stitchcheck.classifier.ensemble import top2
from stitchcheck.qc.counts import map_part
from stitchcheck.spark import inspect as hybrid

SEAM_PROMPT = "seam"
PART_PROMPTS = ("collar", "pocket", "hem")  # the parts tested on the shirt photos (docs/design.md)
PART_T = 0.7
SEAM_T = 0.5  # provisional: set on the Kadiri reference pairs plus the shirt photos before the phone set is scored
N_TILES = 3
# Tile side / seam thickness in StitchingNet training images, measured with the same SAM prompt: median 51
# (IQR 48 to 54, 12 good seams across the 8 training fabrics; results/smoke/seam_scale.json).
TILE_PER_THICKNESS = 51
TILE_MIN = 64  # px in the original photo
GOOD = "good"


def clean_photo(data):
    """Bytes from the phone -> RGB image upright, with no EXIF (so no GPS) when saved."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    return im.convert("RGB")


def localize(im, sam, seam_t=SEAM_T, part_t=PART_T):
    """Run SAM for the seam and the parts in parallel. Returns the best seam object above seam_t (or None), the
    best part above part_t (or None) and the timing."""
    t0 = time.time()
    prompts = (SEAM_PROMPT, *PART_PROMPTS)
    with ThreadPoolExecutor(len(prompts)) as pool:
        found = dict(zip(prompts, pool.map(lambda p: sam(im, p)[0], prompts)))
    seams = [o for o in found[SEAM_PROMPT] if o.get("confidence", 0) >= seam_t and "mask" in o]
    seam = max(seams, key=lambda o: o["confidence"], default=None)
    parts = [(p, o["confidence"]) for p in PART_PROMPTS for o in found[p] if o.get("confidence", 0) >= part_t]
    part, part_conf = max(parts, key=lambda x: x[1], default=(None, None))
    return dict(seam=seam, part=part, part_conf=part_conf, seconds=round(time.time() - t0, 2))


def seam_tiles(im, mask, n=N_TILES, tile_min=TILE_MIN, per_thickness=TILE_PER_THICKNESS):
    """n square tiles centred along the seam mask, each rotated so the seam runs horizontally, with side
    per_thickness x the seam's thickness (mask area / length), so stitches appear at the training scale."""
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], 1).astype(float)
    mean = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - mean, full_matrices=False)
    u = vt[0]
    t = (pts - mean) @ u
    length = t.max() - t.min() + 1
    side = int(min(max(per_thickness * mask.sum() / length, tile_min), min(im.size)))
    angle = math.degrees(math.atan2(u[1], u[0]))
    tiles = []
    for q in (np.arange(n) + 0.5) / n:
        target = t.min() + q * (t.max() - t.min())
        near = np.abs(t - target) <= max(side / 4, 2)
        cx, cy = pts[near].mean(0) if near.any() else mean + target * u
        half = side * 0.75  # room to rotate without empty corners
        box = (int(cx - half), int(cy - half), int(cx + half), int(cy + half))
        big = im.crop(box).rotate(angle, resample=Image.BICUBIC)
        c = big.size[0] / 2
        tiles.append(big.crop((int(c - side / 2), int(c - side / 2), int(c + side / 2), int(c + side / 2))))
    return tiles


def config_hash():
    """Identifies the evaluated configuration a row was produced with."""
    lock = Path("results/test/lock.json")
    parts = [lock.read_text() if lock.exists() else "", SEAM_PROMPT, PART_PROMPTS, PART_T, SEAM_T, N_TILES, TILE_MIN,
             TILE_PER_THICKNESS]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:12]


def inspect_photo(data, *, sam, ensemble, client, reference, line_plan, out_dir, part_hint=None):
    """Run one photo end to end. Returns a dict for the inspection row and the UI (no database writes here).
    retake is set when no seam clears the threshold; then nothing else is filled in."""
    timings, t0 = {}, time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    im = clean_photo(data)
    key = hashlib.sha256(data).hexdigest()[:16]
    photo_path = out_dir / f"{key}.jpg"
    im.save(photo_path, quality=92)
    timings["prepare"] = round(time.time() - t0, 2)

    loc = localize(im, sam)
    timings["sam"] = loc["seconds"]
    part = loc["part"] or part_hint
    if loc["seam"] is None:
        return dict(retake="Move closer to the stitching", photo_path=str(photo_path), seam_found=False,
                    timings=timings)

    t1 = time.time()
    tiles = seam_tiles(im, loc["seam"]["mask"])
    probs = ensemble(tiles)
    worst = int(np.argmax(1 - probs[:, ensemble_classes(ensemble).index(GOOD)]))
    crop_path = out_dir / f"{key}_tile{worst}.jpg"
    tiles[worst].save(crop_path, quality=92)
    t2 = top2(probs[worst])
    timings["classifier"] = round(time.time() - t1, 2)

    t1 = time.time()
    try:
        verdict = hybrid.inspect(client, crop_path, reference, t2)
    except Exception as ex:  # Spark down: the classifier's label goes to the checker, and the line keeps moving
        verdict = dict(pred=hybrid.ASK_CHECKER, proposed=t2[0][0], severity=hybrid.severity(t2[0][0], None),
                       reason=None, spark_error=f"{type(ex).__name__}: {ex}"[:200], tool_called=False)
    timings["spark"] = round(time.time() - t1, 2)
    operator_id, machine_id = map_part(line_plan, part) if part else (None, None)
    timings["total"] = round(time.time() - t0, 2)
    return dict(
        photo_path=str(photo_path), crop_path=str(crop_path), seam_found=True, seam_conf=loc["seam"]["confidence"],
        sam_part=loc["part"], sam_part_conf=loc["part_conf"], part=part, classifier_top2=t2,
        tile_probs=probs.round(4).tolist(), proposed_label=verdict["proposed"], proposed_severity=verdict["severity"],
        needs_checker=verdict["pred"] == hybrid.ASK_CHECKER, reason=verdict.get("reason"),
        spark_verdict={k: verdict.get(k) for k in ("spark_verdict", "spark_label", "confidence", "protected", "pred",
                                                   "tool_called", "raw", "spark_error")},
        tokens=(verdict.get("prompt_tokens"), verdict.get("completion_tokens")),
        operator_id=operator_id, machine_id=machine_id, config_hash=config_hash(), timings=timings)


def ensemble_classes(ensemble):
    from stitchcheck.classifier.train import CLASSES
    return getattr(ensemble, "classes", CLASSES["clean"])
