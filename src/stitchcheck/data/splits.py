"""Leave-2-fabrics-out split for StitchingNet-Seg (CC BY 4.0 images only).

python -m stitchcheck.data.splits   (needs data/manifests/stitchingnet_seg.csv from stitchcheck.data.manifest)

Writes data/splits/stitchingnet_v2.csv with one row per image:
  split         train | val | test | unseen_test | unused | dropped_dup
                  test        = 5 locked classes on the held-out fabrics
                  unseen_test = the 3 other Seg defect classes on the held-out fabrics (never trained on)
                  unused      = the 3 other Seg defect classes on train/val fabrics
  spark_subset  1 for the fixed sample the Spark arms run on: class-balanced from `test`
                plus a small balanced sample from `unseen_test`
"""
import argparse
from pathlib import Path

import pandas as pd

from stitchcheck.data.stitchingnet import CC_BY, SPLIT_FILE

TEST_FABRICS = ("Cotton", "Oxford")   # one Poly-thread, one Core-thread fabric; both shirting
VAL_FABRICS = ("Linen",)              # model selection on an unseen fabric, never a random image split
SPARK_PER_CELL = 42                   # per (label, test fabric): 5 x 2 x 42 = 420
SPARK_UNSEEN_PER_CELL = 10            # per (unseen class, test fabric): 3 x 2 x 10 = 60
SEED = 0

MANIFEST = Path("data/manifests/stitchingnet_seg.csv")
OUT = SPLIT_FILE


def assign_split(row):
    if row.duplicate:
        return "dropped_dup"
    held_out = row.fabric in TEST_FABRICS
    if not row.label:
        return "unseen_test" if held_out else "unused"
    if held_out:
        return "test"
    return "val" if row.fabric in VAL_FABRICS else "train"


def balanced_sample(df, by, n, seed):
    return df.groupby(by, group_keys=False).apply(lambda g: g.sample(min(n, len(g)), random_state=seed))


def build(manifest):
    assert (manifest["licence"] == CC_BY).all(), "the split is built from CC BY images only"
    df = manifest.fillna({"label": ""}).sort_values("path").reset_index(drop=True)
    df["duplicate"] = df.duplicated("md5", keep="first")
    df["split"] = [assign_split(r) for r in df.itertuples()]
    spark = pd.concat([
        balanced_sample(df[df["split"] == "test"], ["label", "fabric"], SPARK_PER_CELL, SEED),
        balanced_sample(df[df["split"] == "unseen_test"], ["source_class", "fabric"], SPARK_UNSEEN_PER_CELL, SEED),
    ])
    df["spark_subset"] = df.index.isin(spark.index).astype(int)
    return df[["path", "fabric", "thread", "source_class", "label", "licence", "split", "spark_subset"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    df = build(pd.read_csv(args.manifest))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    shown = df.assign(label=df["label"].replace("", "(unseen)"))
    print(pd.crosstab(shown["split"], shown["label"], margins=True).to_string(), "\n")
    print("spark_subset by split:", df[df["spark_subset"] == 1]["split"].value_counts().to_dict())
    print("licence by split:\n" + pd.crosstab(df["split"], df["licence"]).to_string())


if __name__ == "__main__":
    main()
