"""Run the per-photo pipeline against the real APIs on a folder of photos (shirt photos now, Kadiri pilot later).

python scripts/smoke/pipeline_photos.py [FOLDER] [--name shirt]

Real SAM 3.1, the 11-fabric demo ensemble and Muse Spark through inspect(). The good reference is the training-fabric
fallback until the Kadiri reference pairs arrive. Crops go to data/cache/pipeline/; a summary without images goes to
results/smoke/pipeline_<name>.json.
"""
import argparse
import json
import time
from pathlib import Path

from stitchcheck.backend.pipeline import SEAM_T, inspect_photo, part_chip, clean_photo
from stitchcheck.classifier.ensemble import Ensemble
from stitchcheck.classifier.train import CLASSES
from stitchcheck.data.stitchingnet import image_path, load_split
from stitchcheck.meta_api import sam_segment, spark_client

LINE_PLAN = [dict(part=p, operator_id=f"OP{i:02d}", machine_id=f"M{i:02d}")
             for i, p in enumerate(["collar", "placket", "cuff", "sleeve", "side_seam", "pocket", "hem", "shoulder"], 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="data/smoke/shirt")
    ap.add_argument("--name", default="shirt")
    args = ap.parse_args()
    split = load_split()
    reference = image_path(split.query("split == 'train' and fabric == 'Satin' and label == 'good'")
                           .sample(1, random_state=0)["path"].item())
    ensemble, client = Ensemble(), spark_client()
    rows = []
    for f in sorted(Path(args.folder).iterdir()):
        if f.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        t0 = time.time()
        part = f.stem if f.stem in {row['part'] for row in LINE_PLAN} else None  # sequence part, from the file name
        r = inspect_photo(f.read_bytes(), sam=sam_segment, ensemble=ensemble, client=client, reference=reference,
                          line_plan=LINE_PLAN, out_dir=Path("data/cache/pipeline") / args.name, part=part,
                          classes=CLASSES["clean"])
        r |= part_chip(clean_photo(f.read_bytes())[0], sam_segment, part)
        r = {k: v for k, v in r.items() if k not in ("photo_path", "crop_path")} | dict(photo=f.name)
        r.get("spark_verdict", {}).pop("raw", None)
        rows.append(r)
        print(f"{f.name}: {r.get('retake') or r['proposed_label']} | part {r.get('part')} | seam "
              f"{r.get('seam_conf')} | scale {r.get('scale_path')} pitch {r.get('stitch_pitch')} side {r.get('tile_side')} | top2 {r.get('classifier_top2')} | "
              f"checker {r.get('needs_checker')} | chip {r.get('part_chip')} | step1 {r.get('step1_seconds')}s step2 "
              f"{r.get('step2_seconds')}s, all {time.time() - t0:.1f}s", flush=True)
    out = Path("results/smoke") / f"pipeline_{args.name}.json"
    out.write_text(json.dumps(dict(seam_threshold=SEAM_T, seam_threshold_status="provisional",
                                   reference="training-fabric fallback (Satin)", runs=rows), indent=2, default=str))
    print("wrote", out)


if __name__ == "__main__":
    main()
