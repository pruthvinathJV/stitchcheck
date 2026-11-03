"""The per-photo pipeline (docs/backend_spec.md, section 3). The phone-set evaluation runs the same code.

Step 1, shown first: photo -> clean copy (EXIF rotation applied, metadata dropped) -> SAM 3.1 "seam" -> tiles
along the best seam, rotated so the seam runs horizontally as in StitchingNet, and cut to hold as many stitches
as a StitchingNet tile -> classifier ensemble on every tile; the tile most likely to be a defect is the one shown.
Step 2, while the screen says "reviewing": inspect() (Muse Spark with the classifier's top 2 and a good
reference) -> proposed label, severity, reason, needs_checker; the part from the inspection sequence maps to the
line plan. Off the critical path: SAM part prompts, shown only as a suggestion chip when they disagree with the
sequence; they never change the part.
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

from stitchcheck.backend.stitch import stitch_pitch
from stitchcheck.classifier.ensemble import top2
from stitchcheck.qc.counts import map_part
from stitchcheck.spark import inspect as hybrid

SEAM_PROMPT = "seam"
PART_PROMPTS = ("collar", "pocket", "hem")  # the parts tested on the shirt photos (docs/design.md)
PART_T = 0.7
SEAM_T = 0.5  # kept on the Kadiri reference pairs (results/val/phone_ref_calibration.json, docs/design.md)
N_TILES = 3
# Stitches in a 224 px StitchingNet training tile: 224 / median stitch pitch (21 px) over 135 good training seams
# where a pitch was found (results/val/stitch_scale.json). Phone tiles are cut to hold the same number.
STITCHES_PER_TILE = 10.67
PROBE_FRAC = 0.3  # probe tiles for measuring the pitch: this share of the photo's short side
FALLBACK_FRAC = 0.15  # provisional tile side when there is neither a pitch nor a hand calibration
# Hand calibration on reference pair A-1-5 at the 15 cm capture distance: stitches counted over a known pixel span
# (scripts/val/hand_scale.py). Used when the pitch is not confident, scaled by resolution and focal length.
HAND_CALIBRATION = Path("results/val/stitch_hand_calibration.json")
CAL_MIN_SIDE = 640  # the calibration is for phone photos; smaller images (224 px lab swatches) are measured instead
FOCAL_35MM_TAG, FOCAL_TAG = 41989, 37386  # EXIF FocalLengthIn35mmFilm, FocalLength
TILE_MIN = 64
GOOD = "good"
Image.MAX_IMAGE_PIXELS = 60_000_000  # larger than any phone photo; beyond it Pillow refuses (decompression bombs)


class NotAnImage(Exception):
    """The upload is not a photo Pillow can decode, or has too many pixels."""


class SeamFinderDown(Exception):
    """SAM could not be reached or failed."""


def clean_photo(data):
    """Bytes from the phone -> (RGB image upright, with no EXIF so no GPS when saved; focal lengths from EXIF).
    Raises NotAnImage for anything Pillow cannot decode or a picture over MAX_IMAGE_PIXELS."""
    try:
        raw = Image.open(io.BytesIO(data))
        if raw.width * raw.height > Image.MAX_IMAGE_PIXELS:
            raise Image.DecompressionBombError(f"{raw.width}x{raw.height}")
        exif = raw.getexif().get_ifd(0x8769) | dict(raw.getexif())
        meta = dict(focal_35mm=_num(exif.get(FOCAL_35MM_TAG)), focal_mm=_num(exif.get(FOCAL_TAG)))
        return ImageOps.exif_transpose(raw).convert("RGB"), meta
    except Image.DecompressionBombError as ex:
        raise NotAnImage("photo has too many pixels, use the normal camera size") from ex
    except (OSError, SyntaxError, ValueError, EOFError) as ex:  # UnidentifiedImageError and truncated files are OSError
        raise NotAnImage("not an image") from ex


def _num(v):
    try:
        return float(v) or None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def hand_calibration():
    return json.loads(HAND_CALIBRATION.read_text()) if HAND_CALIBRATION.exists() else None


def find_seam(im, sam, seam_t=SEAM_T):
    """The best "seam" object above seam_t, or None."""
    objs = [o for o in sam(im, SEAM_PROMPT)[0] if o.get("confidence", 0) >= seam_t and "mask" in o]
    return max(objs, key=lambda o: o["confidence"], default=None)


def suggest_part(im, sam, part_t=PART_T):
    """Background part check: the best of the tested parts above part_t, or (None, None)."""
    with ThreadPoolExecutor(len(PART_PROMPTS)) as pool:
        found = dict(zip(PART_PROMPTS, pool.map(lambda p: sam(im, p)[0], PART_PROMPTS)))
    parts = [(p, o["confidence"]) for p in PART_PROMPTS for o in found[p] if o.get("confidence", 0) >= part_t]
    return max(parts, key=lambda x: x[1], default=(None, None))


def seam_tiles(im, mask, side, n=N_TILES):
    """n square tiles of the given side centred along the seam mask, each rotated so the seam runs horizontally."""
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], 1).astype(float)
    mean = pts.mean(0)
    u = np.linalg.svd(pts - mean, full_matrices=False)[2][0]
    t = (pts - mean) @ u
    angle = math.degrees(math.atan2(u[1], u[0]))
    side = int(max(min(side, min(im.size)), TILE_MIN))
    tiles = []
    for q in (np.arange(n) + 0.5) / n:
        target = t.min() + q * (t.max() - t.min())
        near = np.abs(t - target) <= max(side / 4, 2)
        cx, cy = pts[near].mean(0) if near.any() else mean + target * u
        half = side * 0.75  # room to rotate without empty corners
        big = im.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))).rotate(angle, resample=Image.BICUBIC)
        c = big.size[0] / 2
        tiles.append(big.crop((int(c - side / 2), int(c - side / 2), int(c + side / 2), int(c + side / 2))))
    return tiles


def tile_side(im, mask, meta=None, calibration=None):
    """Side that holds STITCHES_PER_TILE stitches, and how it was found. Returns (side, pitch px, path):
    - "fixed": the hand calibration's pixels per stitch, scaled by the photo's long side and, when both photos have
      it, the 35 mm-equivalent focal length (same 15 cm capture distance assumed). Used for every phone-sized photo
      (long side at least CAL_MIN_SIDE) when a calibration exists: on the Kadiri reference pairs the measured pitch locked onto the weave or the check pattern instead;
    - "pitch": measured on probe tiles along the seam (median of those where it is confident), without a calibration;
    - "provisional": neither is available."""
    cal = calibration if calibration is not None else hand_calibration()
    if cal and max(im.size) >= CAL_MIN_SIDE:
        pitch = cal["px_per_stitch"] * max(im.size) / cal["long_side"]
        f, f_ref = (meta or {}).get("focal_35mm"), cal.get("focal_35mm")
        if f and f_ref:
            pitch *= f / f_ref
        return int(round(STITCHES_PER_TILE * pitch)), pitch, "fixed"
    probes = seam_tiles(im, mask, side=PROBE_FRAC * min(im.size))
    pitches = [r["pitch"] for r in (stitch_pitch(np.array(p)) for p in probes) if r["found"]]
    if pitches:
        pitch = float(np.median(pitches))
        return int(round(STITCHES_PER_TILE * pitch)), pitch, "pitch"
    return int(FALLBACK_FRAC * min(im.size)), None, "provisional"


def seam_overlay(im, mask, max_side=1024):
    """The photo with SAM's seam mask tinted green, for the result card (shows the checker what SAM found)."""
    small = im.copy()
    small.thumbnail((max_side, max_side))
    m = Image.fromarray((mask * 255).astype(np.uint8)).resize(small.size, Image.NEAREST)
    green = Image.new("RGB", small.size, (40, 220, 90))
    return Image.composite(Image.blend(small, green, 0.55), small, m)


def config_hash():
    """Identifies the evaluated configuration a row was produced with."""
    lock = Path("results/test/lock.json")
    parts = [lock.read_text() if lock.exists() else "", SEAM_PROMPT, SEAM_T, N_TILES, STITCHES_PER_TILE, PROBE_FRAC,
             FALLBACK_FRAC, TILE_MIN, hand_calibration()]
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:12]


def step1(data, *, sam, ensemble, out_dir, classes):
    """Photo -> seam crop and classifier label (the first screen). retake is set when no seam clears the
    threshold; then nothing else is filled in. Also returns the cleaned image for the later steps. Raises NotAnImage
    for an upload that is not a usable photo and SeamFinderDown when SAM fails."""
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    im, meta = clean_photo(data)
    key = hashlib.sha256(data).hexdigest()[:16]
    photo_path = out_dir / f"{key}.jpg"
    im.save(photo_path, quality=92)
    try:
        seam = find_seam(im, sam)
    except Exception as ex:
        raise SeamFinderDown(f"{type(ex).__name__}: {ex}"[:200]) from ex
    if seam is None:
        return dict(retake="Move closer to the stitching", photo_path=str(photo_path), seam_found=False,
                    step1_seconds=round(time.time() - t0, 2)), im
    side, pitch, scale_path = tile_side(im, seam["mask"], meta)
    tiles = seam_tiles(im, seam["mask"], side)
    probs = ensemble(tiles)
    worst = int(np.argmax(1 - probs[:, classes.index(GOOD)]))
    crop_path = out_dir / f"{key}_tile{worst}.jpg"
    tiles[worst].save(crop_path, quality=92)
    overlay_path = out_dir / f"{key}_seam.jpg"
    seam_overlay(im, seam["mask"]).save(overlay_path, quality=88)
    return dict(photo_path=str(photo_path), crop_path=str(crop_path), overlay_path=str(overlay_path), seam_found=True, seam_conf=seam["confidence"],
                tile_side=side, stitch_pitch=pitch, scale_path=scale_path, focal_35mm=meta["focal_35mm"],
                classifier_top2=top2(probs[worst]),
                tile_probs=probs.round(4).tolist(), config_hash=config_hash(),
                step1_seconds=round(time.time() - t0, 2)), im


def step2(s1, *, brains, mode, reference, line_plan, part):
    """The judgment brain's review of the step-1 crop (Muse Spark, or Muse Spark Open by choice or as the fallback;
    stitchcheck.backend.brains), and the line-plan mapping of the sequence part."""
    t0 = time.time()
    t2 = s1["classifier_top2"]
    brain, fell_back = None, False
    try:
        v, b, fell_back = brains.call(mode, lambda b: hybrid.inspect(b.client, s1["crop_path"], reference, t2,
                                                                    model=b.model, effort=b.effort))
        brain = b.name
    except Exception as ex:  # no brain answered: the classifier's label goes to the checker, and the line keeps moving
        v = dict(pred=hybrid.ASK_CHECKER, proposed=t2[0][0], severity=hybrid.severity(t2[0][0], None),
                 spark_error=f"{type(ex).__name__}: {ex}"[:200], tool_called=False)
    operator_id, machine_id = map_part(line_plan, part) if part else (None, None)
    return dict(proposed_label=v["proposed"], proposed_severity=v["severity"], needs_checker=v["pred"] == hybrid.ASK_CHECKER,
                reason=v.get("reason"), part=part, operator_id=operator_id, machine_id=machine_id,
                spark_verdict={k: v.get(k) for k in ("spark_verdict", "spark_label", "confidence", "protected", "pred",
                                                     "tool_called", "raw", "spark_error", "cached")},
                tokens=[v.get("prompt_tokens"), v.get("completion_tokens")], brain=brain, fell_back=fell_back,
                step2_seconds=round(time.time() - t0, 2))


def part_chip(im, sam, sequence_part):
    """SAM's part suggestion, shown only when it disagrees with the inspection sequence."""
    part, conf = suggest_part(im, sam)
    return dict(sam_part=part, sam_part_conf=conf, part_chip=part if part and part != sequence_part else None)


def inspect_photo(data, *, sam, ensemble, client, reference, line_plan, out_dir, part, classes):
    """Steps 1 and 2 in one call (the phone-set evaluation and scripts), with Muse Spark only: no fallback, so a
    scored result always comes from the evaluated brain. The part check is not included."""
    from stitchcheck.backend.brains import SPARK, Brains
    s1, _ = step1(data, sam=sam, ensemble=ensemble, out_dir=out_dir, classes=classes)
    if not s1["seam_found"]:
        return s1
    return s1 | step2(s1, brains=Brains({SPARK: client}), mode=SPARK, reference=reference, line_plan=line_plan, part=part)
