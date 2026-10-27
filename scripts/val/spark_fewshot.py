"""Pick the few-shot design for Muse Spark on the Linen validation fabric only (never the test fabrics).

python scripts/val/spark_fewshot.py [--per-class 25]

Same seeded Linen sample as spark_settings.py, one image to inspect per request, 448 px. Designs:
  zero_shot          no references (the chosen zero-shot setting, minimal effort)
  good_ref           one good-seam reference
  good_plus_defects  one good reference plus one per defect class (skipped, broken, crooked, stain)
good_plus_defects runs at minimal and low effort. References come from the training fabrics only: the query's
fabric when it is a training fabric, otherwise one training fabric drawn with the seed (Linen is never a training
fabric, so every Linen query uses that fabric). One fixed reference set per run, drawn with the seed.
Writes results/val/spark_fewshot_runs.csv, spark_fewshot_summary.csv and spark_fewshot_refs.csv.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from stitchcheck.data.stitchingnet import LABELS, image_path, load_split
from stitchcheck.eval.metrics import major_recall, per_class_report
from stitchcheck.meta_api import spark_client
from stitchcheck.spark.classify import classify

OUT = Path("results/val")
SEED, SIZE = 0, 448
DEFECTS = ["skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage"]
DESIGNS = [("zero_shot", "minimal"), ("good_ref", "minimal"), ("good_plus_defects", "minimal"),
           ("good_plus_defects", "low")]


def pick_refs(split, query_fabric):
    train = split.query("split == 'train'")
    fabrics = sorted(train["fabric"].unique())
    fabric = query_fabric if query_fabric in fabrics else np.random.default_rng(SEED).choice(fabrics)
    pool = train[train["fabric"] == fabric]
    pick = lambda label: pool[pool["label"] == label].sample(1, random_state=SEED)["path"].item()
    return fabric, {label: pick(label) for label in ["good"] + DEFECTS}


def macro_f1(g):
    return per_class_report(g, LABELS).set_index("cls").loc["macro", "f1"]


def recall(g, cls):
    pos = g[g["label"] == cls]
    return (pos["pred"] == cls).mean()


def paired_ci(a, b, metric, n_boot=2000):
    """95% CI of metric(b) - metric(a), resampling images (rows are aligned by path)."""
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(a))
    draws = [metric(b.iloc[i]) - metric(a.iloc[i]) for i in (rng.choice(idx, len(idx)) for _ in range(n_boot))]
    return round(metric(b) - metric(a), 3), round(float(np.quantile(draws, 0.025)), 3), round(float(np.quantile(draws, 0.975)), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=25)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    split = load_split()
    val = split.query("split == 'val'")
    sample = val.groupby("label", group_keys=False).sample(args.per_class, random_state=SEED)
    refs = {f: pick_refs(split, f) for f in sample["fabric"].unique()}
    client = spark_client()

    def run(job):
        row, design, effort = job
        fabric, paths = refs[row.fabric]
        shots = {"zero_shot": [], "good_ref": ["good"], "good_plus_defects": ["good"] + DEFECTS}[design]
        try:
            res = classify(client, image_path(row.path), size=SIZE, effort=effort,
                           refs=[(label, image_path(paths[label])) for label in shots])
        except Exception as ex:
            res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
        return dict(path=row.path, label=row.label, design=design, effort=effort, ref_fabric=fabric, **res)

    jobs = [(row, d, e) for d, e in DESIGNS for row in sample.itertuples()]
    with ThreadPoolExecutor(args.workers) as pool:
        df = pd.DataFrame(pool.map(run, jobs))
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "spark_fewshot_runs.csv", index=False)
    pd.DataFrame([dict(query_fabric=q, ref_fabric=f, ref_label=label, path=p)
                  for q, (f, paths) in refs.items() for label, p in paths.items()]).to_csv(OUT / "spark_fewshot_refs.csv", index=False)
    print(f"api errors: {(df['pred'] == 'api_error').sum()}")

    rows, groups = [], {k: g.sort_values("path").reset_index(drop=True) for k, g in df.groupby(["design", "effort"], sort=False)}
    base = groups[("zero_shot", "minimal")]
    for (design, effort), g in groups.items():
        rep = per_class_report(g, LABELS).set_index("cls")
        row = dict(design=design, effort=effort, n=len(g), macro_f1=round(rep.loc["macro", "f1"], 3),
                   **{f"recall_{c}": round(rep.loc[c, "recall"], 3) for c in LABELS},
                   skipped_called_good=round((g[g["label"] == "skipped_stitch"]["pred"] == "good").mean(), 3),
                   major_recall=round(major_recall(g), 3), api_errors=int((g["pred"] == "api_error").sum()),
                   parse_errors=int((g["pred"] == "parse_error").sum()),
                   median_s=round(g["seconds"].median(), 2), p90_s=round(g["seconds"].quantile(0.9), 2),
                   mean_input_tokens=round(g["prompt_tokens"].mean()), mean_output_tokens=round(g["completion_tokens"].mean()))
        if (design, effort) != ("zero_shot", "minimal"):
            row["macro_f1_vs_zero_shot"] = paired_ci(base, g, macro_f1)
            row["skipped_recall_vs_zero_shot"] = paired_ci(base, g, lambda x: recall(x, "skipped_stitch"))
        rows.append(row)
    lo, mi = groups[("good_plus_defects", "low")], groups[("good_plus_defects", "minimal")]
    rows[-1]["macro_f1_low_vs_minimal"] = paired_ci(mi, lo, macro_f1)
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "spark_fewshot_summary.csv", index=False)
    print(summary.T.to_string())


if __name__ == "__main__":
    main()
