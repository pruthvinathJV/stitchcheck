"""Metrics for arms A to D on the StitchingNet test, from the saved predictions (results/test/arm_*.csv).

python scripts/test/analyse.py

Metrics are the ones fixed in docs/design.md (Metrics). Images are resampled for the 95% CIs (2,000 draws, seed 0);
StitchingNet frames of the same defect are correlated, so these intervals are on the narrow side. Arm D is reported
twice: its auto label (disagreements count as ask_checker) and the label shown to the checker. Arm C is reported on
the full test split and on the Spark subset; comparisons between arms use the subset, paired by image.
Writes results/test/summary.csv, per_class.csv and paired.csv.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from stitchcheck.data.stitchingnet import LABELS
from stitchcheck.eval.metrics import GOOD, checker_workload, major_recall, per_class_report

OUT = Path("results/test")
PRICE_IN, PRICE_OUT = 1.25 / 1e6, 4.25 / 1e6  # muse-spark-1.3 standard, USD per token
PHOTOS_PER_GARMENT = 2.1  # collar + placket + a spot check on 1 garment in 10 (docs/design.md)
N_BOOT, SEED = 2000, 0


def macro_f1(g):
    return per_class_report(g, LABELS).set_index("cls").loc["macro", "f1"]


def binary_acc(g):
    return float(((g["pred"] != GOOD) == (g["label"] != GOOD)).mean())


def boot(df, metric, other=None):
    """Point estimate and 95% CI of metric(df), or of metric(other) - metric(df) when other is given (paired)."""
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(df))
    f = (lambda i: metric(df.iloc[i])) if other is None else (lambda i: metric(other.iloc[i]) - metric(df.iloc[i]))
    draws = [f(rng.choice(idx, len(idx))) for _ in range(N_BOOT)]
    return round(float(f(idx)), 3), round(float(np.nanquantile(draws, 0.025)), 3), round(float(np.nanquantile(draws, 0.975)), 3)


def systems():
    arms = {a: pd.read_csv(OUT / f"arm_{a}.csv").sort_values("path").reset_index(drop=True) for a in "ABCD"}
    c = arms["C"]
    sub = set(arms["A"]["path"])
    yield "C classifier (full split)", c
    yield "C classifier (subset)", c[c["path"].isin(sub)].reset_index(drop=True)
    yield "A zero-shot Spark", arms["A"]
    yield "B Spark + good reference", arms["B"]
    yield "D hybrid, auto label", arms["D"]
    yield "D hybrid, shown to checker", arms["D"].assign(pred=arms["D"]["proposed"])


def main():
    rows, per_class, frames = [], [], {}
    for name, df in systems():
        frames[name] = df
        known, unseen = df[df["split"] == "test"], df[df["split"] == "unseen_test"]
        r = dict(system=name, n_test=len(known), n_unseen=len(unseen),
                 major_recall=boot(known, major_recall),
                 auto_major_recall=boot(known, lambda g: major_recall(g, include_deferral=False)),
                 checker_workload=boot(known, checker_workload),
                 macro_f1=boot(known, macro_f1), binary_acc=boot(known, binary_acc),
                 sent_to_checker=round(float((known["pred"] == "ask_checker").mean()), 3),
                 unseen_called_good=round(float((unseen["pred"] == GOOD).mean()), 3),
                 unknown_flag_rate=round(float((unseen["pred"] == "unknown_defect").mean()), 3))
        if "seconds" in df:
            r |= dict(median_s=round(df["seconds"].median(), 2), p90_s=round(df["seconds"].quantile(0.9), 2))
            cost = (df["prompt_tokens"] * PRICE_IN + df["completion_tokens"] * PRICE_OUT).mean()
            r |= dict(cost_per_100_photos=round(100 * cost, 3),
                      cost_per_100_garments=round(100 * PHOTOS_PER_GARMENT * cost, 3))
        rows.append(r)
        per_class.append(per_class_report(known, LABELS).assign(system=name))

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "summary.csv", index=False)
    pd.concat(per_class).to_csv(OUT / "per_class.csv", index=False)

    base = frames["C classifier (subset)"]
    pairs = []
    for name in ("A zero-shot Spark", "B Spark + good reference", "D hybrid, auto label", "D hybrid, shown to checker"):
        a, b = base[base["split"] == "test"].reset_index(drop=True), frames[name][frames[name]["split"] == "test"].reset_index(drop=True)
        assert (a["path"] == b["path"]).all()
        pairs.append(dict(comparison=f"{name} minus C", major_recall=boot(a, major_recall, b),
                          checker_workload=boot(a, checker_workload, b), macro_f1=boot(a, macro_f1, b)))
    a = frames["A zero-shot Spark"][frames["A zero-shot Spark"]["split"] == "test"].reset_index(drop=True)
    b = frames["B Spark + good reference"][frames["B Spark + good reference"]["split"] == "test"].reset_index(drop=True)
    pairs.append(dict(comparison="B minus A", major_recall=boot(a, major_recall, b),
                      checker_workload=boot(a, checker_workload, b), macro_f1=boot(a, macro_f1, b)))
    paired = pd.DataFrame(pairs)
    paired.to_csv(OUT / "paired.csv", index=False)

    pd.set_option("display.width", 250)
    print(summary.T.to_string())
    print()
    print(pd.concat(per_class).pivot(index="cls", columns="system", values="recall").round(3).to_string())
    print()
    print(paired.to_string(index=False))


if __name__ == "__main__":
    main()
