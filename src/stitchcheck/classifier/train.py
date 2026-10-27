"""Fine-tune an ImageNet CNN on StitchingNet (leave-2-fabrics-out split, 8 training fabrics).

python -m stitchcheck.classifier.train --variant clean [--arch efficientnet_b0] [--holdout FABRIC | --all-fabrics]

clean: 5 classes, CC BY 4.0 images from StitchingNet-Seg only (the split loader asserts it).
Validates on Linen (split == "val"). With --holdout, validates on one training fabric instead and trains on
the other 7 (per-fabric difficulty without touching the test fabrics). Test splits are never loaded, except
with --all-fabrics: the demo model, trained on all 11 fabrics with no validation; its weights are the last epoch's.
Otherwise best epoch by validation macro-F1. Weights -> models/<run>/best.pt (gitignored); metrics -> results/classifier/<run>/.

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

from stitchcheck.data.stitchingnet import LABELS, image_path, load_split
from stitchcheck.eval.metrics import per_class_report

CLASSES = {"clean": LABELS}  # the only variant; the name is kept in run names
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


AUGS = ("basic", "strong", "trivial")


def train_transform(aug="basic"):
    """basic: brightness/contrast jitter (phone lighting). strong: adds colour, grayscale and blur, because
    unseen fabrics differ mostly in colour and texture. trivial: TrivialAugmentWide. Flips in all: seams run
    either way."""
    geometric = [transforms.RandomResizedCrop(224, scale=(0.6, 1.0), ratio=(0.8, 1.25)),
                 transforms.RandomHorizontalFlip(), transforms.RandomVerticalFlip()]
    photometric = {
        "basic": [transforms.ColorJitter(brightness=0.4, contrast=0.4)],
        "strong": [transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.05),
                   transforms.RandomGrayscale(0.2),
                   transforms.RandomApply([transforms.GaussianBlur(5, sigma=(0.1, 2.0))], p=0.3)],
        "trivial": [transforms.TrivialAugmentWide()],
    }[aug]
    return transforms.Compose(geometric + photometric + [transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


def eval_transform():
    return transforms.Compose([transforms.Resize(224), transforms.CenterCrop(224),
                               transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


class Images(Dataset):
    def __init__(self, df, classes, transform):
        self.paths = [image_path(p) for p in df["path"]]
        self.y = [classes.index(l) if l in classes else -1 for l in df["label"]]
        self.transform = transform

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, i):
        return self.transform(Image.open(self.paths[i]).convert("RGB")), self.y[i]


def select(variant, holdout=None, all_fabrics=False):
    """Return (train_df, val_df)."""
    df = load_split()
    if all_fabrics:
        train = df[df["split"].isin(["train", "val", "test"]) & df["label"].isin(CLASSES[variant])]
        return train, train.iloc[:0]
    df = df[df["split"].isin(["train", "val"])]  # test and unseen_test are never read here
    if holdout:
        df = df[df["split"] == "train"]
        assert holdout in set(df["fabric"]), f"{holdout} is not a training fabric"
        is_val = df["fabric"] == holdout
    else:
        is_val = df["split"] == "val"
    classes = CLASSES[variant]
    return df[~is_val & df["label"].isin(classes)], df[is_val & df["label"].isin(classes)]


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
    ap.add_argument("--all-fabrics", action="store_true", help="demo model: train on all 11 fabrics, no validation")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--backbone-lr-mult", type=float, default=1.0, help="backbone lr = lr x this; the head gets lr")
    ap.add_argument("--aug", choices=AUGS, default="basic")
    ap.add_argument("--label-smoothing", type=float, default=0.1)
    ap.add_argument("--tag", default="", help="suffix for the run name, e.g. a sweep config id")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true", help="continue from models/<run>/last.pt if it exists")
    ap.add_argument("--stall-minutes", type=float, default=10)
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    classes = CLASSES[args.variant]
    run = (f"{args.variant}_{args.arch}_{11 if args.all_fabrics else 8}fab" + (f"_{args.tag}" if args.tag else "")
           + (f"_holdout-{args.holdout.replace(' ', '')}" if args.holdout else ""))
    res_dir, model_dir = Path("results/classifier") / run, Path("models") / run
    res_dir.mkdir(parents=True, exist_ok=True); model_dir.mkdir(parents=True, exist_ok=True)

    assert not (args.all_fabrics and args.holdout), "--all-fabrics has no validation fabric"
    train, val = select(args.variant, args.holdout, args.all_fabrics)
    loader = lambda df, tf, shuffle: DataLoader(Images(df, classes, tf), batch_size=args.batch, shuffle=shuffle,
                                                num_workers=args.workers)
    train_dl = loader(train, train_transform(args.aug), True)
    val_dl = loader(val, eval_transform(), False)
    print(f"{run}: train {len(train)} ({train['fabric'].nunique()} fabrics), val {len(val)} "
          f"({', '.join(sorted(val['fabric'].unique()))}), device {device}",
          flush=True)

    model = build_model(args.arch, len(classes)).to(device)
    head = list(model.classifier.parameters())
    head_ids = {id(q) for q in head}
    backbone = [q for q in model.parameters() if id(q) not in head_ids]
    lrs = [args.lr * args.backbone_lr_mult, args.lr]
    opt = torch.optim.AdamW([{"params": backbone, "lr": lrs[0]}, {"params": head, "lr": lrs[1]}], weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lrs, total_steps=args.epochs * len(train_dl), pct_start=0.1)
    loss_fn = nn.CrossEntropyLoss(label_smoothing=args.label_smoothing)

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
        macro_f1 = None
        if len(val):
            probs = predict(model, val_dl, device, watchdog)
            pred = [classes[i] for i in probs.argmax(1)]
            macro_f1 = float(per_class_report(val.assign(pred=pred), classes).set_index("cls").loc["macro", "f1"])
        history.append(dict(epoch=epoch, train_loss=total / len(train), val_macro_f1=macro_f1, seconds=round(time.time() - t0)))
        print(history[-1], flush=True)
        if macro_f1 is None or macro_f1 > best[0]:  # no validation: keep the latest epoch
            best = (macro_f1 if macro_f1 is not None else -1.0, epoch)
            torch.save(dict(state_dict=model.state_dict(), classes=classes, arch=args.arch), model_dir / "best.pt")
        torch.save(dict(model=model.state_dict(), opt=opt.state_dict(), sched=sched.state_dict(),
                        rng=torch.get_rng_state(), history=history, best=list(best), epoch=epoch), last_path)
        watchdog.beat()

    if not len(val):
        pd.DataFrame(history).to_csv(res_dir / "history.csv", index=False)
        (res_dir / "metrics.json").write_text(json.dumps(dict(
            run=run, variant=args.variant, arch=args.arch, val_fabrics=[], train_fabrics=sorted(train["fabric"].unique()),
            n_train=len(train), n_val=0, final_epoch=args.epochs, config=vars(args)), indent=2, default=str))
        print(f"saved {model_dir / 'best.pt'} (last epoch; no validation)")
        return
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
    pd.DataFrame(history).to_csv(res_dir / "history.csv", index=False)
    (res_dir / "metrics.json").write_text(json.dumps(out, indent=2, default=str))
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
