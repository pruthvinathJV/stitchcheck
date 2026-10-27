"""3-seed ensembles (mean softmax) of the winning setting, scored on Linen validation, plus the demo-model check.

python scripts/train/ensemble.py --bench RUN RUN RUN [--demo RUN RUN RUN]

--bench: the 8-fabric runs, each kept at the same final epoch. Reports each seed's Linen macro-F1 and the
         ensemble's at that epoch, labelled optimistic: the setting was chosen on Linen.
--demo:  the 11-fabric runs. Linen is in their training data, so this is not a score: each seed must agree with
         the benchmark ensemble on at least 90% of Linen predictions, as a check that no seed went bad.
Writes results/classifier/ensemble.json.
"""
import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from stitchcheck.classifier.ensemble import load_model as load
from stitchcheck.classifier.train import CLASSES, Images, eval_transform, predict, select
from stitchcheck.eval.metrics import per_class_report

MIN_AGREEMENT = 0.90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", nargs=3, required=True)
    ap.add_argument("--demo", nargs=3)
    args = ap.parse_args()
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    classes = CLASSES["clean"]
    _, val = select("clean")
    dl = DataLoader(Images(val, classes, eval_transform()), batch_size=64, num_workers=0)
    macro = lambda probs: float(per_class_report(val.assign(pred=[classes[i] for i in probs.argmax(1)]), classes)
                                .set_index("cls").loc["macro", "f1"])

    bench = {run: predict(load(run, device), dl, device) for run in args.bench}
    ens = sum(bench.values()) / len(bench)
    epochs = {json.loads((Path("results/classifier") / run / "metrics.json").read_text())["best_epoch"] for run in args.bench}
    assert len(epochs) == 1, f"benchmark seeds stopped at different epochs: {epochs}"
    out = dict(val="Linen", n=len(val), epoch=epochs.pop(),
               note="optimistic: the setting was chosen on Linen", bench={run: round(macro(p), 4) for run, p in bench.items()},
               bench_ensemble=round(macro(ens), 4))
    if args.demo:
        agree = {run: round(float((predict(load(run, device), dl, device).argmax(1) == ens.argmax(1)).mean()), 4)
                 for run in args.demo}
        out.update(demo_agreement=agree, demo_min_agreement=MIN_AGREEMENT,
                   demo_ok=all(a >= MIN_AGREEMENT for a in agree.values()))
    Path("results/classifier/ensemble.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    if args.demo and not out["demo_ok"]:
        raise SystemExit("a demo seed agrees with the benchmark ensemble on fewer than 90% of Linen predictions")


if __name__ == "__main__":
    main()
