"""Export a 3-seed classifier ensemble to one ONNX graph (mean of softmax) and check it against PyTorch.

python scripts/export/onnx_ensemble.py [--runs demo|bench] [--out models/demo_ensemble.onnx]

The graph takes normalised 224 x 224 tiles (batch of any size) and returns the mean softmax over the seeds, the same
as stitchcheck.classifier.ensemble.Ensemble. Parity: max absolute difference in probabilities and label agreement on
100 Linen validation images. Timing on CPU: session load (cold start), first call and steady calls for a batch of 3
tiles (one photo). Writes results/export/<name>.json; the .onnx file is gitignored.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from PIL import Image

from stitchcheck.classifier.ensemble import BENCH_RUNS, DEMO_RUNS, Ensemble, load_model
from stitchcheck.classifier.train import eval_transform
from stitchcheck.data.stitchingnet import image_path, load_split


class MeanSoftmax(torch.nn.Module):
    def __init__(self, models):
        super().__init__()
        self.models = torch.nn.ModuleList(models)

    def forward(self, x):
        return torch.stack([torch.softmax(m(x), 1) for m in self.models]).mean(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", choices=["demo", "bench"], default="demo")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    runs = DEMO_RUNS if args.runs == "demo" else BENCH_RUNS
    out = args.out or Path(f"models/{args.runs}_ensemble.onnx")
    model = MeanSoftmax([load_model(r, "cpu").eval() for r in runs]).eval()
    torch.onnx.export(model, torch.zeros(1, 3, 224, 224), out, input_names=["tiles"], output_names=["probs"],
                      dynamic_axes={"tiles": {0: "n"}, "probs": {0: "n"}}, opset_version=17, dynamo=False)

    val = load_split().query("split == 'val'").sample(100, random_state=0)
    images = [Image.open(image_path(p)).convert("RGB") for p in val["path"]]
    x = torch.stack([eval_transform()(im) for im in images]).numpy()
    t0 = time.time()
    sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    load_s = time.time() - t0
    t0 = time.time()
    sess.run(None, {"tiles": x[:3]})
    first_s = time.time() - t0
    steady = []
    for i in range(10):
        t0 = time.time()
        sess.run(None, {"tiles": x[3 * i:3 * i + 3]})
        steady.append(time.time() - t0)
    onnx_probs = np.concatenate([sess.run(None, {"tiles": x[i:i + 20]})[0] for i in range(0, len(x), 20)])
    torch_probs = Ensemble(runs, dev="cpu")(images)
    result = dict(runs=runs, onnx=str(out), size_mb=round(out.stat().st_size / 1e6, 1),
                  parity_max_abs_diff=float(np.abs(onnx_probs - torch_probs).max()),
                  parity_label_agreement=float((onnx_probs.argmax(1) == torch_probs.argmax(1)).mean()),
                  cpu_session_load_s=round(load_s, 2), cpu_first_call_s=round(first_s, 3),
                  cpu_steady_call_s_median=round(float(np.median(steady)), 3), tiles_per_call=3,
                  cpu_threads=torch.get_num_threads(), note="local Mac CPU; the hosted container is timed at deploy")
    Path("results/export").mkdir(parents=True, exist_ok=True)
    (Path("results/export") / f"{out.stem}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
