"""Hand calibration of stitch scale on a reference pair photo (A-1-5, 15 cm capture distance).

python scripts/val/hand_scale.py grid PHOTO                      gridded copy (lines every 100 px) to pick a span
python scripts/val/hand_scale.py zoom PHOTO X0 Y0 X1 Y1          zoomed crop with ticks every 50 px, for counting
python scripts/val/hand_scale.py record PHOTO X0 X1 STITCHES     write results/val/stitch_hand_calibration.json

Counting is done by eye on the zoomed crop: the number of whole stitches between x = X0 and x = X1 along a seam
that runs roughly horizontally in the upright photo. Reference pairs only, never test pairs. Images go to
data/cache/hand_scale/, never to git.
"""
import json
import sys
from pathlib import Path

from PIL import ImageDraw

from stitchcheck.backend.pipeline import HAND_CALIBRATION, clean_photo

OUT = Path("data/cache/hand_scale")


def load(photo):
    return clean_photo(Path(photo).read_bytes())


def grid(photo):
    im, _ = load(photo)
    d = ImageDraw.Draw(im)
    for x in range(0, im.width, 100):
        d.line([(x, 0), (x, im.height)], fill=(255, 0, 0) if x % 500 else (255, 255, 0), width=2)
        d.text((x + 3, 3), str(x), fill=(255, 255, 0))
    for y in range(0, im.height, 100):
        d.line([(0, y), (im.width, y)], fill=(255, 0, 0) if y % 500 else (255, 255, 0), width=2)
        d.text((3, y + 3), str(y), fill=(255, 255, 0))
    return im


def zoom(photo, x0, y0, x1, y1):
    im, _ = load(photo)
    crop = im.crop((x0, y0, x1, y1))
    scale = max(1, 1600 // crop.width)
    crop = crop.resize((crop.width * scale, crop.height * scale))
    d = ImageDraw.Draw(crop)
    for x in range(0, x1 - x0 + 1, 50):
        d.line([(x * scale, 0), (x * scale, 20)], fill=(255, 0, 0), width=3)
        d.text((x * scale + 3, 22), str(x0 + x), fill=(255, 0, 0))
    return crop


def record(photo, x0, x1, stitches):
    im, meta = load(photo)
    out = dict(photo=Path(photo).name, long_side=max(im.size), image_size=list(im.size), focal_35mm=meta["focal_35mm"],
               focal_mm=meta["focal_mm"], span_px=[x0, x1], stitches=stitches, px_per_stitch=(x1 - x0) / stitches,
               capture_distance_cm=15, note="counted by eye on a reference pair before any test pair is scored")
    HAND_CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
    HAND_CALIBRATION.write_text(json.dumps(out, indent=2))
    return out


def main():
    cmd, photo, *nums = sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    if cmd == "grid":
        path = OUT / f"{Path(photo).stem}_grid.jpg"
        grid(photo).save(path, quality=90)
    elif cmd == "zoom":
        x0, y0, x1, y1 = map(int, nums)
        path = OUT / f"{Path(photo).stem}_zoom_{x0}_{x1}.jpg"
        zoom(photo, x0, y0, x1, y1).save(path, quality=92)
    elif cmd == "record":
        x0, x1, n = map(float, nums)
        print(json.dumps(record(photo, int(x0), int(x1), n), indent=2))
        return
    else:
        raise SystemExit(__doc__)
    print(path)


if __name__ == "__main__":
    main()
