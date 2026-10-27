"""The 3-seed classifier ensembles (mean softmax) used by the benchmark and the app."""
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from stitchcheck.classifier.train import CLASSES, Images, build_model, eval_transform, predict

BENCH_RUNS = [f"clean_convnext_tiny_8fab_final12-seed{s}" for s in range(3)]  # 8 fabrics: benchmark arms C and D
DEMO_RUNS = [f"clean_convnext_tiny_11fab_final12-seed{s}" for s in range(3)]  # 11 fabrics: the app


def device():
    return "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"


def load_model(run, dev):
    ck = torch.load(Path("models") / run / "best.pt", map_location=dev, weights_only=False)
    model = build_model(ck["arch"], len(ck["classes"]), pretrained=False)
    model.load_state_dict(ck["state_dict"])
    return model.to(dev)


def ensemble_probs(df, runs=BENCH_RUNS, dev=None):
    """Mean softmax over the runs for every row of df (needs a path column); rows in df order, columns in
    CLASSES['clean'] order."""
    dev = dev or device()
    classes = CLASSES["clean"]
    df = df.assign(label=df["label"].where(df["label"].isin(classes), classes[0]))  # labels are not used here
    dl = DataLoader(Images(df, classes, eval_transform()), batch_size=64, num_workers=0)
    return sum(predict(load_model(run, dev), dl, dev) for run in runs) / len(runs)


class Ensemble:
    """The models loaded once, for in-memory crops (PIL images): ensemble(images) -> mean softmax per image."""

    def __init__(self, runs=DEMO_RUNS, dev=None):
        self.dev = dev or device()
        self.models = [load_model(run, self.dev).eval() for run in runs]
        self.transform = eval_transform()
        self.runs = list(runs)

    @torch.no_grad()
    def __call__(self, images):
        x = torch.stack([self.transform(im.convert("RGB")) for im in images]).to(self.dev)
        return sum(torch.softmax(m(x), 1) for m in self.models).cpu().numpy() / len(self.models)


def top2(probs):
    classes = CLASSES["clean"]
    order = np.argsort(-probs)[:2]
    return [(classes[i], float(probs[i])) for i in order]
