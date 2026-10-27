"""The single prompt revision: visual label definitions vs the current prompt, Linen validation only.

python scripts/val/spark_prompt_revision.py [--per-class 25]

Few-shot design (a) (one good reference, 448 px, minimal effort) with each prompt, on a fresh seeded Linen sample
that shares no image with the 125 used to choose the size, effort and few-shot design. Rule, fixed before the run:
keep the revision only if skipped-stitch recall or major-defect recall improves with a 95% CI above zero, and the
share of good seams flagged does not rise with a CI above zero. Writes results/val/spark_prompt_revision_*.csv.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from spark_fewshot import OUT, SEED, SIZE, paired_ci, pick_refs, recall  # noqa: E402

from stitchcheck.data.stitchingnet import LABELS, image_path, load_split  # noqa: E402
from stitchcheck.eval.metrics import checker_workload, major_recall, per_class_report  # noqa: E402
from stitchcheck.meta_api import spark_client  # noqa: E402
from stitchcheck.spark.classify import VISUAL_PROMPT, ZERO_SHOT_PROMPT, classify  # noqa: E402

FRESH_SEED = 1
PROMPTS = {"current": ZERO_SHOT_PROMPT, "visual": VISUAL_PROMPT}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=25)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    split = load_split()
    val = split.query("split == 'val'")
    used = val.groupby("label", group_keys=False).sample(args.per_class, random_state=SEED)
    sample = val.drop(used.index).groupby("label", group_keys=False).sample(args.per_class, random_state=FRESH_SEED)
    assert not set(sample["path"]) & set(used["path"])
    refs = {f: pick_refs(split, f) for f in sample["fabric"].unique()}
    client = spark_client()

    def run(job):
        row, name = job
        _, paths = refs[row.fabric]
        try:
            res = classify(client, image_path(row.path), size=SIZE, effort="minimal", prompt=PROMPTS[name],
                           refs=[("good", image_path(paths["good"]))])
        except Exception as ex:
            res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
        return dict(path=row.path, label=row.label, prompt=name, **res)

    with ThreadPoolExecutor(args.workers) as pool:
        df = pd.DataFrame(pool.map(run, [(row, p) for p in PROMPTS for row in sample.itertuples()]))
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "spark_prompt_revision_runs.csv", index=False)
    print(f"api errors: {(df['pred'] == 'api_error').sum()}")

    g = {k: x.sort_values("path").reset_index(drop=True) for k, x in df.groupby("prompt")}
    rows = []
    for name, x in g.items():
        rep = per_class_report(x, LABELS).set_index("cls")
        rows.append(dict(prompt=name, n=len(x), macro_f1=round(rep.loc["macro", "f1"], 3),
                         **{f"recall_{c}": round(rep.loc[c, "recall"], 3) for c in LABELS},
                         skipped_called_good=round((x[x["label"] == "skipped_stitch"]["pred"] == "good").mean(), 3),
                         major_recall=round(major_recall(x), 3), good_flagged=round(checker_workload(x), 3),
                         median_s=round(x["seconds"].median(), 2), mean_input_tokens=round(x["prompt_tokens"].mean()),
                         mean_output_tokens=round(x["completion_tokens"].mean())))
    cur, vis = g["current"], g["visual"]
    d_skip = paired_ci(cur, vis, lambda x: recall(x, "skipped_stitch"))
    d_major = paired_ci(cur, vis, major_recall)
    d_good = paired_ci(cur, vis, checker_workload)
    keep = (d_skip[1] > 0 or d_major[1] > 0) and not d_good[1] > 0
    rows[-1].update(skipped_recall_vs_current=d_skip, major_recall_vs_current=d_major, good_flagged_vs_current=d_good,
                    macro_f1_vs_current=paired_ci(cur, vis, lambda x: per_class_report(x, LABELS).set_index("cls").loc["macro", "f1"]),
                    keep=keep)
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "spark_prompt_revision_summary.csv", index=False)
    print(summary.T.to_string())
    print("decision:", "keep the visual prompt" if keep else "keep the current prompt")


if __name__ == "__main__":
    main()
