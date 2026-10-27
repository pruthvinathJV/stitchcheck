"""Stage 2 of the classifier search: rerun the top 3 Stage 1 settings with a second seed, pick the best mean.

python scripts/train/stage2.py top3     prints trainer args for the seed-1 runs still missing (one line each)
python scripts/train/stage2.py winner   writes results/classifier/stage2_selection.csv and selection.json,
                                        prints trainer args for the winning setting
python scripts/train/stage2.py final    prints the 6 final-model jobs: the winning setting, seeds 0 to 2, on 8 fabrics
                                        (benchmark) and on all 11 (demo), each stopped after epoch N

Stage 1 ranks each setting by its seed-0 Linen macro-F1. The winner has the best mean over seeds 0 and 1;
ties go to the smaller model. N is the median best epoch over every seed of the winning setting (a .5 rounds up),
so final models never pick an epoch on Linen. Only Linen validation runs on 8 fabrics with frozen BatchNorm are read
(the base setting since the Oct 27 seed check; see docs/design.md).
"""
import json
import math
import statistics
import sys
from pathlib import Path

import pandas as pd

RESULTS = Path("results/classifier")
SETTING = ["arch", "aug", "lr", "backbone_lr_mult", "freeze_bn"]
PARAMS_M = {"efficientnet_b0": 5.3, "mobilenet_v3_large": 5.5, "convnext_tiny": 28.6}  # millions, for ties


def linen_runs():
    rows = []
    for f in sorted(RESULTS.glob("clean_*_8fab*/metrics.json")):
        m = json.loads(f.read_text())
        c = m["config"]
        if c.get("holdout") or m["val_fabrics"] != ["Linen"] or not c.get("freeze_bn") or c.get("stop_after"):
            continue
        macro = next(r["f1"] for r in m["per_class"] if r["cls"] == "macro")
        rows.append(dict(run=m["run"], tag=c.get("tag", ""), seed=c["seed"], arch=c["arch"], aug=c.get("aug", "basic"),
                         lr=c["lr"], backbone_lr_mult=c.get("backbone_lr_mult", 1.0), freeze_bn=True,
                         best_epoch=m["best_epoch"], macro_f1=macro))
    return pd.DataFrame(rows)


def top3(df):
    return df[df["seed"] == 0].sort_values("macro_f1", ascending=False).head(3)


def args_for(row, seed=None, tag=None):
    out = f"--arch {row.arch} --aug {row.aug} --lr {row.lr} --backbone-lr-mult {row.backbone_lr_mult} --freeze-bn"
    return out + (f" --seed {seed} --tag {tag}" if seed is not None else "")


def main(cmd):
    df = linen_runs()
    top = top3(df)
    if cmd == "top3":
        for row in top.itertuples():
            done = df.merge(pd.DataFrame([row._asdict()])[SETTING], on=SETTING)
            if not (done["seed"] == 1).any():
                print(args_for(row, 1, f"{row.tag or 'base'}-seed1"))
        return
    table = select_winner(df, top)
    win = table.iloc[0]
    if cmd == "final":
        for seed in range(3):
            print(f"--variant clean {args_for(win)} --stop-after {win.stop_after} --seed {seed} --tag final-seed{seed} --workers 8")
            print(f"--variant clean {args_for(win)} --stop-after {win.stop_after} --seed {seed} --tag final-seed{seed} --all-fabrics --workers 8")
        return
    table.to_csv(RESULTS / "stage2_selection.csv", index=False)
    (RESULTS / "selection.json").write_text(json.dumps(
        dict(win[["setting"] + SETTING + ["macro_f1_mean", "best_epochs", "stop_after"]].to_dict(),
             selected_on="Linen validation, seeds 0 and 1"), indent=2, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    print(args_for(win), end="")
    print(table.to_string(index=False), file=sys.stderr)


def select_winner(df, top):
    scores = df.merge(top[SETTING + ["tag"]].rename(columns={"tag": "setting"}), on=SETTING)
    scores["macro_f1_01"] = scores["macro_f1"].where(scores["seed"] <= 1)  # the mean uses seeds 0 and 1 only
    table = (scores.groupby(["setting"] + SETTING).agg(seeds=("seed", lambda s: sorted(s)), macro_f1_mean=("macro_f1_01", "mean"),
                                                       macro_f1_runs=("macro_f1", list),
                                                       best_epochs=("best_epoch", lambda e: sorted(e))).reset_index())
    assert (table["seeds"].apply(len) >= 2).all(), "every top-3 setting needs seeds 0 and 1"
    table["params_m"] = table["arch"].map(PARAMS_M)
    table["stop_after"] = table["best_epochs"].apply(lambda e: int(math.floor(statistics.median(e) + 0.5)))
    return table.sort_values(["macro_f1_mean", "params_m"], ascending=[False, True])


if __name__ == "__main__":
    main(sys.argv[1])
