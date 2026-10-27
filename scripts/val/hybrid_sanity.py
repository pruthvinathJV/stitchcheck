"""Sanity check of the hybrid inspector (arm D) against the classifier alone, Linen validation only.

python scripts/val/hybrid_sanity.py [--per-class 25] [--effort minimal]

Same 125 Linen images as spark_settings.py and spark_fewshot.py. The classifier is the 8-fabric benchmark ensemble;
its Linen numbers are optimistic because the setting was chosen on Linen. Writes results/val/hybrid_sanity_*.csv.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from spark_fewshot import OUT, SEED, paired_ci, pick_refs, recall  # noqa: E402

from stitchcheck.classifier.ensemble import ensemble_probs, top2  # noqa: E402
from stitchcheck.data.stitchingnet import LABELS, image_path, load_split  # noqa: E402
from stitchcheck.eval.metrics import checker_workload, major_recall, per_class_report  # noqa: E402
from stitchcheck.meta_api import spark_client  # noqa: E402
from stitchcheck.spark.inspect import inspect  # noqa: E402


def macro_f1(g, col="pred"):
    return per_class_report(g.assign(pred=g[col]), LABELS).set_index("cls").loc["macro", "f1"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=25)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--effort", default="minimal")
    args = ap.parse_args()

    split = load_split()
    sample = (split.query("split == 'val'").groupby("label", group_keys=False)
              .sample(args.per_class, random_state=SEED).sort_values("path").reset_index(drop=True))
    probs = ensemble_probs(sample)
    refs = {f: pick_refs(split, f) for f in sample["fabric"].unique()}
    client = spark_client()

    def run(i):
        row = sample.iloc[i]
        t2 = top2(probs[i])
        try:
            res = inspect(client, image_path(row.path), image_path(refs[row.fabric][1]["good"]), t2, effort=args.effort)
        except Exception as ex:
            res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
        return dict(path=row.path, label=row.label, classifier=t2[0][0], classifier_p=t2[0][1], **res)

    with ThreadPoolExecutor(args.workers) as pool:
        df = pd.DataFrame(pool.map(run, range(len(sample))))
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.effort == "minimal" else f"_{args.effort}"
    df.to_csv(OUT / f"hybrid_sanity_runs{suffix}.csv", index=False)
    print(f"api errors: {(df['pred'] == 'api_error').sum()}, tool skipped: {(~df['tool_called'].astype(bool)).sum()}")

    clf = df.assign(pred=df["classifier"])
    rows = []
    for name, g in (("classifier", clf), ("hybrid", df), ("hybrid_proposal", df.assign(pred=df["proposed"]))):
        rows.append(dict(system=name, macro_f1=round(macro_f1(g), 3), major_recall=round(major_recall(g), 3),
                         auto_major_recall=round(major_recall(g, include_deferral=False), 3),
                         skipped_recall=round(recall(g, "skipped_stitch"), 3),
                         good_flagged=round(checker_workload(g), 3),
                         sent_to_checker=round((g["pred"] == "ask_checker").mean(), 3)))
    summary = pd.DataFrame(rows)
    summary.loc[1, "major_recall_vs_classifier"] = str(paired_ci(clf, df, major_recall))
    summary.loc[1, "good_flagged_vs_classifier"] = str(paired_ci(clf, df, checker_workload))
    summary.loc[2, "proposal_macro_f1_vs_classifier"] = str(paired_ci(clf, df.assign(pred=df["proposed"]), macro_f1))
    summary.to_csv(OUT / f"hybrid_sanity_summary{suffix}.csv", index=False)
    print(summary.T.to_string())
    print("median s", df["seconds"].median(), "| protected", int(df["protected"].sum()),
          "| Spark label = classifier", round((df["spark_label"] == df["classifier"]).mean(), 3))


if __name__ == "__main__":
    main()
