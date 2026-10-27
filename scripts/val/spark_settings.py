"""Pick Muse Spark settings on the Linen validation fabric only (never the test fabrics).

python scripts/val/spark_settings.py [--per-class 25] [--efforts default low minimal] [--sizes 224 448]

Runs zero-shot Spark, one image per request, on a seeded per-class sample of the val split for every
effort x size combination. Writes results/val/spark_settings_runs.csv and spark_settings_summary.csv.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from stitchcheck.data.stitchingnet import STITCHINGNET_DIR
from stitchcheck.eval.metrics import checker_workload, major_recall
from stitchcheck.meta_api import spark_client
from stitchcheck.spark.classify import classify

OUT = Path("results/val")
SEED = 0


def summarise(df):
    rows = []
    for (effort, size), g in df.groupby(["effort", "size"], sort=False):
        rows.append(dict(
            effort=effort, size=size, n=len(g),
            accuracy=round((g["pred"] == g["label"]).mean(), 3),
            defect_vs_good_acc=round(((g["pred"] != "good") == (g["label"] != "good")).mean(), 3),
            major_recall=round(major_recall(g), 3),
            checker_workload=round(checker_workload(g), 3),
            parse_errors=int((g["pred"] == "parse_error").sum()),
            median_s=round(g["seconds"].median(), 2), p90_s=round(g["seconds"].quantile(0.9), 2),
            mean_output_tokens=round(g["completion_tokens"].mean()),
            mean_input_tokens=round(g["prompt_tokens"].mean()),
        ))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=25)
    ap.add_argument("--efforts", nargs="+", default=["default", "low", "minimal"])
    ap.add_argument("--sizes", nargs="+", type=int, default=[224, 448])
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    val = pd.read_csv("data/splits/stitchingnet_v1.csv").query("split == 'val'")
    sample = val.groupby("label", group_keys=False).sample(args.per_class, random_state=SEED)
    client = spark_client()
    jobs = [(row, e, s) for e in args.efforts for s in args.sizes for row in sample.itertuples()]

    def run(job):
        row, effort, size = job
        try:
            res = classify(client, STITCHINGNET_DIR / row.path, size=size, effort=None if effort == "default" else effort)
        except Exception as ex:
            res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
        return dict(path=row.path, label=row.label, effort=effort, size=size, **res)

    with ThreadPoolExecutor(args.workers) as pool:
        df = pd.DataFrame(pool.map(run, jobs))
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "spark_settings_runs.csv", index=False)
    summary = summarise(df[df["pred"] != "api_error"])
    summary.to_csv(OUT / "spark_settings_summary.csv", index=False)
    print(f"api errors: {(df['pred'] == 'api_error').sum()}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
