"""Licence audit of the CC BY-NC StitchingNet release against StitchingNet-Seg: counts, sizes, duplicates,
licence coverage, sample grids. A one-off check (Oct 25); nothing downstream reads its outputs.

python -m stitchcheck.data.audit [--root DIR] [--seg-root DIR] [--out results/audit]

Writes a manifest of the NC release to data/cache/ (gitignored) and tables + grids under --out.
Grids are derived from CC BY-NC images and are gitignored.
"""
import argparse
import json
import random
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image

from stitchcheck.data.stitchingnet import LABELS, SEG_DIR, STITCHINGNET_DIR, scan, tag_licence

MANIFEST = Path("data/cache/stitchingnet_nc_audit.csv")


def duplicate_stats(df, key):
    groups = [g for _, g in df.groupby(key) if len(g) > 1]
    return dict(
        groups=len(groups),
        images=sum(len(g) for g in groups),
        removable=sum(len(g) - 1 for g in groups),
        cross_fabric_groups=sum(g["fabric"].nunique() > 1 for g in groups),
        cross_class_groups=sum(g["source_class"].nunique() > 1 for g in groups),
    )


def sample_grid(df, root, rows, cell, n_cols, title, out_path, seed=0):
    """rows: list of (row_label, sub-dataframe). Draws up to n_cols random images per row."""
    rng = random.Random(seed)
    fig, axes = plt.subplots(len(rows), n_cols, figsize=(n_cols * cell, len(rows) * cell), squeeze=False)
    for i, (row_label, sub) in enumerate(rows):
        picks = rng.sample(list(sub["path"]), min(n_cols, len(sub)))
        for j, ax in enumerate(axes[i]):
            ax.axis("off")
            if j < len(picks):
                ax.imshow(Image.open(root / picks[j]).convert("RGB"))
        axes[i][0].set_title(f"{row_label} (n={len(sub)})", fontsize=7, loc="left")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_path, dpi=100)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=STITCHINGNET_DIR)
    ap.add_argument("--seg-root", type=Path, default=SEG_DIR)
    ap.add_argument("--out", type=Path, default=Path("results/audit"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    df = tag_licence(scan(args.root), args.seg_root)
    df.to_csv(MANIFEST, index=False)

    pd.crosstab([df["fabric"], df["thread"]], df["source_class"], margins=True).to_csv(args.out / "counts_source.csv")
    locked = df[df["label"] != ""]
    counts = pd.crosstab([locked["fabric"], locked["thread"]], locked["label"], margins=True)[LABELS + ["All"]]
    counts.to_csv(args.out / "counts_locked.csv")
    sizes = df.groupby(["width", "height", "mode"]).size().rename("n").reset_index()

    # Licence coverage: how many images per class are usable under CC BY (via Seg) after md5 dedupe.
    uniq = df.drop_duplicates("md5")
    licence = pd.crosstab(uniq["source_class"], uniq["licence"], margins=True)
    licence.to_csv(args.out / "licence_by_class.csv")
    licence_fabric = pd.crosstab([uniq["fabric"]], [uniq["label"].replace("", "(unseen)"), uniq["licence"]])
    licence_fabric.to_csv(args.out / "licence_by_fabric_label.csv")

    rows_by_fabric = lambda d: [(f, d[d["fabric"] == f]) for f in sorted(df["fabric"].unique())]
    for label in LABELS:
        sample_grid(df, args.root, rows_by_fabric(locked[locked["label"] == label]), 1.6, 6,
                    f"{label}: random samples per fabric", args.out / f"grid_{label}.jpg")
    unseen = df[df["label"] == ""]
    sample_grid(df, args.root, [(c, unseen[unseen["source_class"] == c]) for c in sorted(unseen["source_class"].unique())],
                1.6, 8, "unseen defect classes (all fabrics)", args.out / "grid_unseen.jpg")

    summary = dict(
        images=len(df), unique_images=int(df["md5"].nunique()), fabrics=int(df["fabric"].nunique()),
        source_classes=int(df["source_class"].nunique()), locked_images=len(locked),
        locked_unique=int(locked["md5"].nunique()), sizes=sizes.to_dict(orient="records"),
        exact_duplicates=duplicate_stats(df, "md5"), dhash_collisions=duplicate_stats(df, "dhash"),
        cc_by_unique=int((uniq["licence"] == "CC-BY-4.0").sum()),
        min_locked_cell=int(counts.drop(index="All", level=0)[LABELS].values.min()),
    )
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, default=int))
    print(json.dumps(summary, indent=2, default=int))
    print(counts.to_string(), "\n")
    print(licence.to_string())


if __name__ == "__main__":
    main()
