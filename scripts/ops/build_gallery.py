"""Assemble the demo gallery (data/gallery/: images and manifest.json) from local files. Never commit the images.

Sources: our own shirt photos (data/smoke/shirt/*.jpeg) and up to 8 CC BY 4.0 StitchingNet-Seg test-fabric images,
one per class and fabric, drawn with a fixed seed through load_split() (which asserts every row is CC BY).
With --kadiri: our Kadiri phone photos instead, fabric A (white, the demo session's fabric, so its good reference
matches): one photo per seam type, the first in pair and mark order where SAM found the seam in the scored run
(results/phone/runs.csv). The rule never looks at any verdict.
Titles use the label and part words of docs/ui_strings.json.

Run from the repo root: PYTHONPATH=src python scripts/ops/build_gallery.py
"""
import argparse
import json
import random
import shutil
from pathlib import Path

SN_CREDIT, OWN_CREDIT = "StitchingNet-Seg, CC BY 4.0", "StitchCheck"
SN_LABELS = ["skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage", "good"]
SN_PARTS = ["collar", "placket", "cuff", "pocket", "hem", "side_seam", "sleeve", "shoulder"]
FABRIC_TE = {"Cotton": "కాటన్", "Oxford": "ఆక్స్‌ఫర్డ్"}
WHOLE = {"te": "పూర్తి షర్ట్", "en": "Whole shirt"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def pick_stitchingnet(split, max_images=8, seed=0):
    """Test rows, one per (class, fabric), in rounds so every class shows before a class repeats on the other
    fabric. Returns a list of rows (dicts)."""
    test = split[(split["split"] == "test") & (split["label"].isin(SN_LABELS))]
    fabrics = sorted(test["fabric"].unique())
    rng = random.Random(seed)
    order = [(label, fabrics[(i + r) % len(fabrics)]) for r in range(len(fabrics)) for i, label in enumerate(SN_LABELS)]
    rows = []
    for label, fabric in order:
        group = test[(test["label"] == label) & (test["fabric"] == fabric)].sort_values("path")
        if len(group):
            rows.append(group.iloc[rng.randrange(len(group))].to_dict())
        if len(rows) == max_images:
            break
    return rows


def pick_kadiri(runs="results/phone/runs.csv", fabric="A"):
    """(photo_id, label) per seam type: the first photo of the fabric, in pair and mark order, with a seam found."""
    import pandas as pd
    r = pd.read_csv(runs)
    r = r[(r["fabric"] == fabric) & (r["pred_C"] != "retake")].sort_values("photo_id")
    return [(g.iloc[0]["photo_id"], label) for label, g in r.groupby("label", sort=False)]


def build_kadiri(out_dir="data/gallery", strings="docs/ui_strings.json", images="data/phone/images"):
    labels = json.loads(Path(strings).read_text())["labels"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for k, (photo_id, label) in enumerate(pick_kadiri()):
        name = f"kadiri-{photo_id}.jpg"
        shutil.copyfile(Path(images) / f"{photo_id}.jpg", out / name)
        items.append(dict(id=f"kadiri-{photo_id}", title_te=f"{labels[label]['te']}, తెల్ల కాటన్",
                          title_en=f"{labels[label]['en']}, white cotton", part=SN_PARTS[k % len(SN_PARTS)],
                          image=name, credit=OWN_CREDIT + ", Kadiri"))
    return _finish(out, items)


def _finish(out, items):
    keep = {i["image"] for i in items}
    for f in out.iterdir():  # drop images left over from an older build
        if f.suffix.lower() in IMAGE_SUFFIXES and f.name not in keep:
            f.unlink()
    (out / "manifest.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    return items


def build(out_dir="data/gallery", shirt_dir="data/smoke/shirt", strings="docs/ui_strings.json", split=None,
          image_path=None, max_sn=8, seed=0):
    """Copy the images into out_dir and write manifest.json. Returns the manifest entries."""
    if split is None or image_path is None:
        from stitchcheck.data import stitchingnet
        split = stitchingnet.load_split() if split is None else split
        image_path = image_path or stitchingnet.image_path
    words = json.loads(Path(strings).read_text())
    labels, parts = words["labels"], words["parts"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []

    for f in sorted(Path(shirt_dir).glob("*.jpeg")):
        part = f.stem if f.stem in parts else "collar"
        title = {"te": f"షర్ట్ {parts[part]['te']}", "en": f"Shirt {parts[part]['en'].lower()}"} if f.stem in parts else WHOLE
        name = f"shirt-{f.stem}.jpg"
        shutil.copyfile(f, out / name)
        items.append(dict(id=f"shirt-{f.stem}", title_te=title["te"], title_en=title["en"], part=part, image=name,
                          credit=OWN_CREDIT))

    for k, row in enumerate(pick_stitchingnet(split, max_sn, seed)):
        fabric, label = row["fabric"], row["label"]
        ident = f"sn-{fabric.lower()}-{label}"
        name = f"{ident}{Path(row['path']).suffix.lower() or '.jpg'}"
        shutil.copyfile(image_path(row["path"]), out / name)
        items.append(dict(id=ident, title_te=f"{labels[label]['te']}, {FABRIC_TE.get(fabric, fabric)} బట్ట",
                          title_en=f"{labels[label]['en']}, {fabric.lower()} fabric", part=SN_PARTS[k % len(SN_PARTS)],
                          image=name, credit=SN_CREDIT))

    return _finish(out, items)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="data/gallery")
    ap.add_argument("--shirt", default="data/smoke/shirt")
    ap.add_argument("--max-sn", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--kadiri", action="store_true", help="Kadiri phone photos (fabric A) instead")
    a = ap.parse_args()
    items = build_kadiri(a.out) if a.kadiri else build(a.out, a.shirt, max_sn=a.max_sn, seed=a.seed)
    print(f"{len(items)} gallery images in {a.out}")


if __name__ == "__main__":
    main()
