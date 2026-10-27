"""Fine-tune an ImageNet CNN on StitchingNet (leave-2-fabrics-out split, 8 training fabrics).

python -m stitchcheck.classifier.train --variant clean|full [--arch efficientnet_b0] [--holdout FABRIC]

clean: 5 classes, CC BY 4.0 images only (the shipped default). full: 6 classes, adds CC BY-NC puckering.
Validates on Linen (split == "val"). With --holdout, validates on one training fabric instead and trains on
the other 7 (per-fabric difficulty without touching the test fabrics). Test splits are never loaded.
Best epoch by validation macro-F1. Weights -> models/<run>/best.pt (gitignored); metrics -> results/classifier/<run>/.

The MPS backend can hang inside a GPU sync. A watchdog exits with code 75 if no training step finishes for
--stall-minutes; models/<run>/last.pt is saved every epoch, so the caller can rerun with --resume.
"""
import argparse
import json
import os
import random
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

from stitchcheck.data.stitchingnet import STITCHINGNET_DIR
from stitchcheck.eval.metrics import per_class_report

CLASSES = {
    "clean": ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage"],
    "full": ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage"],
}
SPLIT_FILE = Path("data/splits/stitchingnet_v1.csv")
MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
STALLED_EXIT_CODE = 75


class Watchdog:
    """Kill the process if beat() isn't called for `seconds` (a hung GPU sync never returns)."""

    def __init__(self, seconds):
        self.seconds, self.last = seconds, time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def beat(self):
        self.last = time.time()

    def _run(self):
        while True:
            time.sleep(15)
            if time.time() - self.last > self.seconds:
                print(f"STALLED: no progress for {self.seconds}s, exiting with {STALLED_EXIT_CODE}", flush=True)
                os._exit(STALLED_EXIT_CODE)


def build_model(arch, n_classes, pretrained=True):
    if arch == "efficientnet_b0":
        m = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None)
        m.classifier[1] = nn.Linear(m.classifier[1].in_features, n_classes)
    elif arch == "mobilenet_v3_large":
        m = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.IMAGENET1K_V2 if pretrained else None)
        m.classifier[3] = nn.Linear(m.classifier[3].in_features, n_classes)
    elif arch == "convnext_tiny":
        m = models.convnext_tiny(weights=models.ConvNeXt_Tiny_Weights.IMAGENET1K_V1 if pretrained else None)
        m.classifier[2] = nn.Linear(m.classifier[2].in_features, n_classes)
    else:
        raise ValueError(arch)
    return m


def train_transform():
    # Brightness/contrast jitter stands in for phone lighting; flips because seams run either way.
    return transforms.Compose([
        transforms.RandomResizedCrop(224, scale=(0.6, 1.0), ratio=(0.8, 1.25)),
        transforms.RandomHorizontalFlip(), transforms.RandomVerticalFlip(),
        transforms.ColorJitter(brightness=0.4, contrast=0.4),
        transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


def eval_transform():
    return transforms.Compose([transforms.Resize(224), transforms.CenterCrop(224),
                               transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


class Images(Dataset):
    def __init__(self, df, classes, transform):
        self.paths = [STITCHINGNET_DIR / p for p in df["path"]]
        self.y = [classes.index(l) if l in classes else -1 for l in df["label"]]
        self.transform = transform

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        return self.transform(Image.open(self.paths[i]).convert("RGB")), self.y[i]


def select(variant, holdout=None):
    """Return (train_df, val_df, extra_df). extra_df: validation images of classes the model doesn't have
    (puckering for clean), to see what the model calls them."""
    df = pd.read_csv(SPLIT_FILE)
    df = df[df["split"].isin(["train", "val"])]  # test and unseen_test are never read here
    if holdout:
        df = df[df["split"] == "train"]
        assert holdout in set(df["fabric"]), f"{holdout} is not a training fabric"
        is_val = df["fabric"] == holdout
    else:
        is_val = df["split"] == "val"
    classes = CLASSES[variant]
    train, val = df[~is_val & df["label"].isin(classes)], df[is_val & df["label"].isin(classes)]
    extra = df[is_val & df["label"].isin(set(CLASSES["full"]) - set(classes))]
    if variant == "clean":
        assert (train["licence"] == "CC-BY-4.0").all(), "clean model must train on CC BY images only"
    return train, val, extra


@torch.no_grad()
def predict(model, loader, device, watchdog=None):
    model.eval()
    probs = []
    for x, _ in loader:
        probs.append(torch.softmax(model(x.to(device)), 1).cpu())
        if watchdog:
            watchdog.beat()
    return torch.cat(probs).numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=list(CLASSES), required=True)
    ap.add_argument("--arch", default="efficientnet_b0")
    ap.add_argument("--holdout", help="validate on this training fabric instead of Linen")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true", help="continue from models/<run>/last.pt if it exists")
    ap.add_argument("--stall-minutes", type=float, default=10)
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    classes = CLASSES[args.variant]
    run = f"{args.variant}_{args.arch}_8fab" + (f"_holdout-{args.holdout.replace(' ', '')}" if args.holdout else "")
    res_dir, model_dir = Path("results/classifier") / run, Path("models") / run
    res_dir.mkdir(parents=True, exist_ok=True); model_dir.mkdir(parents=True, exist_ok=True)

    train, val, extra = select(args.variant, args.holdout)
    loader = lambda df, tf, shuffle: DataLoader(Images(df, classes, tf), batch_size=args.batch, shuffle=shuffle,
                                                num_workers=args.workers)
    train_dl = loader(train, train_transform(), True)
    val_dl = loader(val, eval_transform(), False)
    print(f"{run}: train {len(train)}, val {len(val)} ({', '.join(sorted(val['fabric'].unique()))}), device {device}",
          flush=True)

    model = build_model(args.arch, len(classes)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * len(train_dl), pct_start=0.1)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.1)

    history, best, start = [], (-1.0, None), 1
    last_path = model_dir / "last.pt"
    if args.resume and last_path.exists():
        state = torch.load(last_path, map_location=device, weights_only=False)
        model.load_state_dict(state["model"]); opt.load_state_dict(state["opt"]); sched.load_state_dict(state["sched"])
        torch.set_rng_state(state["rng"].cpu())
        history, best, start = state["history"], tuple(state["best"]), state["epoch"] + 1
        print(f"resumed after epoch {state['epoch']}", flush=True)

    watchdog = Watchdog(args.stall_minutes * 60)
    for epoch in range(start, args.epochs + 1):
        model.train(); t0 = time.time()
        total = torch.zeros((), device=device)  # summed on the device: no GPU sync per step
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            loss = loss_fn(model(x), y)
            opt.zero_grad(); loss.backward(); opt.step(); sched.step()
            total += loss.detach() * len(y)
            watchdog.beat()
        total = total.item()
        probs = predict(model, val_dl, device, watchdog)
        pred = [classes[i] for i in probs.argmax(1)]
        macro_f1 = float(per_class_report(val.assign(pred=pred), classes).set_index("cls").loc["macro", "f1"])
        history.append(dict(epoch=epoch, train_loss=total / len(train), val_macro_f1=macro_f1, seconds=round(time.time() - t0)))
        print(history[-1], flush=True)
        if macro_f1 > best[0]:
            best = (macro_f1, epoch)
            torch.save(dict(state_dict=model.state_dict(), classes=classes, arch=args.arch), model_dir / "best.pt")
        torch.save(dict(model=model.state_dict(), opt=opt.state_dict(), sched=sched.state_dict(),
                        rng=torch.get_rng_state(), history=history, best=list(best), epoch=epoch), last_path)
        watchdog.beat()

    ckpt = torch.load(model_dir / "best.pt", map_location=device, weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    probs = predict(model, val_dl, device, watchdog)
    preds = val[["path", "fabric", "label"]].assign(pred=[classes[i] for i in probs.argmax(1)], conf=probs.max(1))
    preds.to_csv(res_dir / "val_preds.csv", index=False)
    report = per_class_report(preds, classes)
    out = dict(run=run, variant=args.variant, arch=args.arch, val_fabrics=sorted(val["fabric"].unique()),
               n_train=len(train), n_val=len(val), best_epoch=best[1], config=vars(args),
               per_class=report.to_dict("records"),
               confusion=pd.crosstab(preds["label"], preds["pred"]).to_dict())
    if len(extra):  # e.g. what the clean model calls puckering
        xp = predict(model, loader(extra, eval_transform(), False), device, watchdog)
        out["unseen_class_predictions"] = (extra.assign(pred=[classes[i] for i in xp.argmax(1)])
                                           .groupby("label")["pred"].value_counts().unstack(fill_value=0).to_dict("index"))
    pd.DataFrame(history).to_csv(res_dir / "history.csv", index=False)
    (res_dir / "metrics.json").write_text(json.dumps(out, indent=2, default=str))
    print(report.to_string(index=False))
    if "unseen_class_predictions" in out:
        print("predictions on classes the model lacks:", out["unseen_class_predictions"])


if __name__ == "__main__":
    main()
