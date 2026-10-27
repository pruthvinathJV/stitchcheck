"""Collect every classifier run's validation metrics into one table.

python scripts/train/summarize.py   ->  results/classifier/runs_summary.csv
"""
import json
from pathlib import Path

import pandas as pd

from stitchcheck.eval.metrics import MAJOR_DEFECTS

rows = []
for f in sorted(Path("results/classifier").glob("*/metrics.json")):
    m = json.loads(f.read_text())
    per = {r["cls"]: r for r in m["per_class"]}
    preds = pd.read_csv(f.parent / "val_preds.csv")
    majors = preds[preds["label"].isin(MAJOR_DEFECTS)]
    c = m["config"]
    rows.append(dict(
        run=m["run"], variant=m["variant"], arch=m["arch"], aug=c.get("aug", "basic"), lr=c["lr"],
        backbone_lr_mult=c.get("backbone_lr_mult", 1.0), seed=c["seed"], val=",".join(m["val_fabrics"]),
        best_epoch=m["best_epoch"], macro_f1=per["macro"]["f1"],
        major_recall=round((majors["pred"] != "good").mean(), 4),
        **{f"recall_{k}": round(v["recall"], 4) for k, v in per.items() if k != "macro"}))
out = pd.DataFrame(rows).sort_values(["variant", "val", "macro_f1"], ascending=[True, True, False])
out.to_csv("results/classifier/runs_summary.csv", index=False)
print(out.drop(columns=["variant", "lr", "backbone_lr_mult"]).to_string(index=False))
