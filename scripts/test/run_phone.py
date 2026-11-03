"""Score the Kadiri phone test set once: arms A, B, C and D on the product's seam crop.

python scripts/test/run_phone.py refs      good reference tile per fabric from the {F}-1-5 pairs (reference pairs only)
python scripts/test/run_phone.py score     all arms on the test pairs; refuses unless the tree is clean
python scripts/test/run_phone.py report    metrics with pair-bootstrap intervals

Rules are in docs/design.md ("Phone-set scoring rules"). Labels come from data/phone/manifest.csv after the
skipped-stitch review (results/phone/review.csv). Raw predictions go to results/phone/runs.csv before any metric is
computed; crops go to data/cache/phone_run/, never to git.
"""
import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from analyse import binary_acc, macro_f1  # noqa: E402

from stitchcheck.backend.brains import SPARK, Brains  # noqa: E402
from stitchcheck.backend.pipeline import GOOD, clean_photo, find_seam, seam_tiles, step1, step2, tile_side  # noqa: E402
from stitchcheck.classifier.ensemble import ONNX_DEMO, OnnxEnsemble  # noqa: E402
from stitchcheck.classifier.train import CLASSES  # noqa: E402
from stitchcheck.eval.metrics import checker_workload, major_recall, per_class_report  # noqa: E402
from stitchcheck.meta_api import sam_segment, spark_client  # noqa: E402
from stitchcheck.spark.classify import classify  # noqa: E402

PHONE, REF = Path("data/phone"), Path("data/phone/ref")
REF_TILES = Path("data/phone/ref_tiles")
OUT = Path("results/phone")
CACHE = Path("data/cache/phone_run")
SIZE, EFFORT = 448, "minimal"  # as in the StitchingNet Spark arms (results/test/lock.json)
RETAKE = "retake"
N_BOOT, SEED = 2000, 0
CLASSES5 = CLASSES["clean"]


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def refs():
    ensemble = OnnxEnsemble(ONNX_DEMO)
    REF_TILES.mkdir(parents=True, exist_ok=True)
    man = pd.read_csv(REF / "manifest.csv")
    out = {}
    for fabric, rows in man.groupby("fabric"):
        best = None
        for r in rows.itertuples():
            im, meta = clean_photo((REF / "images" / f"{r.photo_id}.jpg").read_bytes())
            seam = find_seam(im, sam_segment)
            if seam is None:
                continue
            tiles = seam_tiles(im, seam["mask"], tile_side(im, seam["mask"], meta)[0])
            p_good = ensemble(tiles)[:, CLASSES5.index(GOOD)]
            i = int(np.argmax(p_good))
            if best is None or p_good[i] > best[0]:
                best = (float(p_good[i]), r.photo_id, i, tiles[i])
        if best is None:
            raise SystemExit(f"no seam found on any {fabric}-1-5 photo")
        best[3].save(REF_TILES / f"{fabric}-good.jpg", quality=95)
        out[fabric] = dict(photo=best[1], tile=best[2], p_good=round(best[0], 4))
        print(fabric, out[fabric], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ref_tiles.json").write_text(json.dumps(out, indent=2))


def labels():
    """Test photos with their labels after the skipped-stitch review; removed photos are dropped."""
    man = pd.read_csv(PHONE / "manifest.csv")
    review = pd.read_csv(OUT / "review.csv")  # photo_id, decision: keep | good | remove
    assert set(review["photo_id"]) == set(man.loc[man["check_visible"] == 1, "photo_id"]), "review is incomplete"
    man = man.merge(review[["photo_id", "decision"]], on="photo_id", how="left")
    man.loc[man["decision"] == "good", "label"] = GOOD
    return man[man["decision"] != "remove"].drop(columns="decision").reset_index(drop=True)


def score(workers):
    if git("status", "--porcelain", "--untracked-files=no"):  # the untracked PWA files do not touch scoring
        raise SystemExit("working tree has changes: commit them first, so every row names the commit that produced it")
    commit = git("rev-parse", "HEAD")
    test = labels()
    missing = [f for f in test["fabric"].unique() if not (REF_TILES / f"{f}-good.jpg").exists()]
    if missing:
        raise SystemExit(f"no reference tile for {missing}: run refs first")
    ensemble, client = OnnxEnsemble(ONNX_DEMO), spark_client()
    brains = Brains({SPARK: client})

    def one(r):
        data = (PHONE / "images" / f"{r.photo_id}.jpg").read_bytes()
        ref = REF_TILES / f"{r.fabric}-good.jpg"
        row = dict(photo_id=r.photo_id)
        for attempt in range(3):  # transient API errors only
            try:
                s1, _ = step1(data, sam=sam_segment, ensemble=ensemble, out_dir=CACHE, classes=CLASSES5)
                break
            except Exception as ex:
                s1 = dict(error=f"{type(ex).__name__}: {ex}"[:200])
                time.sleep(5 * (attempt + 1))
        if "error" in s1:
            return row | dict(pred_A="api_error", pred_B="api_error", pred_C="api_error", pred_D="api_error",
                              shown_D="api_error", error=s1["error"])
        if not s1["seam_found"]:
            return row | {k: RETAKE for k in ("pred_A", "pred_B", "pred_C", "pred_D", "shown_D")} | dict(
                step1_seconds=s1["step1_seconds"])
        row |= dict(seam_conf=s1["seam_conf"], tile_side=s1["tile_side"], scale_path=s1["scale_path"],
                    stitch_pitch=s1["stitch_pitch"], pred_C=s1["classifier_top2"][0][0],
                    top2_C=json.dumps(s1["classifier_top2"]), step1_seconds=s1["step1_seconds"],
                    config_hash=s1["config_hash"])
        for arm, kw in (("A", {}), ("B", dict(refs=[("good", ref)]))):
            for attempt in range(3):
                try:
                    res = classify(client, s1["crop_path"], size=SIZE, effort=EFFORT, **kw)
                    break
                except Exception as ex:
                    res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:200])
                    time.sleep(5 * (attempt + 1))
            row |= {f"pred_{arm}": res["pred"], f"seconds_{arm}": res.get("seconds"),
                    f"tokens_{arm}": json.dumps([res.get("prompt_tokens"), res.get("completion_tokens")])}
        s2 = step2(s1, brains=brains, mode=SPARK, reference=ref, line_plan=[], part=None)
        v = s2["spark_verdict"]
        row |= dict(pred_D="api_error" if v.get("spark_error") else v["pred"], shown_D=s2["proposed_label"],
                    severity_D=s2["proposed_severity"], spark_label_D=v.get("spark_label"),
                    tool_called_D=v.get("tool_called"), reason_D=s2["reason"], step2_seconds=s2["step2_seconds"],
                    tokens_D=json.dumps(s2["tokens"]), error=v.get("spark_error"))
        return row

    t0 = time.time()
    with ThreadPoolExecutor(workers) as pool:
        rows = []
        for i, row in enumerate(pool.map(one, test.itertuples()), 1):
            rows.append(row)
            print(f"{i}/{len(test)} {row['photo_id']}: C {row['pred_C']} | A {row['pred_A']} | B {row['pred_B']} | "
                  f"D {row['pred_D']} -> {row['shown_D']}", flush=True)
    runs = test[["photo_id", "pair_id", "fabric", "label", "unseen"]].merge(pd.DataFrame(rows), on="photo_id")
    OUT.mkdir(parents=True, exist_ok=True)
    runs.assign(commit=commit).to_csv(OUT / "runs.csv", index=False)
    errors = runs[[c for c in runs if c.startswith(("pred_", "shown_"))]].isin(["api_error"]).sum().to_dict()
    print(f"wrote {OUT / 'runs.csv'}: {len(runs)} photos in {time.time() - t0:.0f} s; api errors {errors}")


def metrics(g, col):
    """One arm's metrics on one (re)sample of photos. A retake is a miss in recall and a tap in workload."""
    pred = g[col]
    seen = g[g["unseen"] == 0]
    caught = g.assign(pred=pred.replace(RETAKE, GOOD))
    return dict(
        major_recall=major_recall(caught),
        auto_major_recall=major_recall(caught, include_deferral=False),
        workload=checker_workload(g.assign(pred=pred)),
        binary_acc=binary_acc(caught.assign(pred=caught["pred"])),
        macro_f1=macro_f1(seen.assign(pred=seen[col])),
        open_seam_called_good=float((caught.loc[g["label"] == "open_seam", "pred"] == GOOD).mean()),
        retake_rate=float((pred == RETAKE).mean()),
    )


def report():
    runs = pd.read_csv(OUT / "runs.csv")
    arms = {"A zero-shot Muse Spark": "pred_A", "B Muse Spark + good reference": "pred_B",
            "C classifier": "pred_C", "D StitchCheck, auto": "pred_D", "D StitchCheck, shown to checker": "shown_D"}
    pairs = runs["pair_id"].unique()
    by_pair = {p: g for p, g in runs.groupby("pair_id")}
    rng = np.random.default_rng(SEED)
    draws = [rng.choice(pairs, len(pairs)) for _ in range(N_BOOT)]
    summary = {}
    for name, col in arms.items():
        point = metrics(runs, col)
        boots = pd.DataFrame([metrics(pd.concat([by_pair[p] for p in d]), col) for d in draws])
        summary[name] = {k: dict(value=round(v, 4), ci95=[round(float(boots[k].quantile(q)), 4) for q in (0.025, 0.975)])
                         for k, v in point.items()}
        per_class_report(runs[runs["unseen"] == 0].assign(pred=runs[col]), CLASSES5).to_csv(
            OUT / f"per_class_{col}.csv", index=False)
    lat = dict(step1_median_s=float(runs["step1_seconds"].median()), step2_median_s=float(runs["step2_seconds"].median()))
    out = dict(n_photos=len(runs), n_pairs=len(pairs), labels=runs["label"].value_counts().to_dict(),
               commit=runs["commit"].iloc[0], latency=lat, arms=summary, bootstrap=dict(n=N_BOOT, seed=SEED, unit="pair"))
    (OUT / "summary.json").write_text(json.dumps(out, indent=2))
    for name, m in summary.items():
        print(f"{name:<34} " + "  ".join(f"{k} {v['value']:.3f} ({v['ci95'][0]:.3f} to {v['ci95'][1]:.3f})"
                                          for k, v in m.items() if k in ("major_recall", "auto_major_recall", "workload", "macro_f1")))
    print(json.dumps(lat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["refs", "score", "report"])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    {"refs": refs, "score": lambda: score(args.workers), "report": report}[args.cmd]()


if __name__ == "__main__":
    main()
