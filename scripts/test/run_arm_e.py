"""Arm E: Muse Spark Open (open weights, Modal shared endpoint) on the locked StitchingNet subset.

python scripts/test/run_arm_e.py [--arms A B D] [--limit N]

The A, B and D equivalents with the locked prompts, image size, references, classifier weights and threshold: the
runner refuses to start unless every locked setting in results/test/lock.json matches (it cannot run from the
test-lock-2 commit, which has no Modal client). One deviation, fixed before any arm E call: reasoning effort is "low",
the lowest the endpoint accepts ("minimal" is rejected). Writes results/test/arm_E{A,B,D}.csv.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "val"))
from run_arms import LOCK, OUT, git, lock_contents  # noqa: E402
from spark_fewshot import SIZE, pick_refs  # noqa: E402

from stitchcheck.classifier.ensemble import top2  # noqa: E402
from stitchcheck.data.stitchingnet import image_path, load_split  # noqa: E402
from stitchcheck.spark import inspect as hybrid  # noqa: E402
from stitchcheck.spark.classify import classify  # noqa: E402

BASE_URL = "https://inference.us-west.modal.direct/v1"
MODEL = "jvpnath--ep-muse-spark-open-816b-a42b-server.us-west.modal.direct"
EFFORT = "low"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", default=["A", "B", "D"])
    ap.add_argument("--limit", type=int, help="first N subset images only (cost check)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if git("status", "--porcelain"):
        raise SystemExit("working tree has changes: commit first, so the run is traceable")
    if json.loads(LOCK.read_text()) != lock_contents():
        raise SystemExit("locked settings differ from results/test/lock.json")
    load_dotenv()
    client = OpenAI(base_url=BASE_URL, api_key=f"{os.environ['MODAL_PROXY_TOKEN_ID']}.{os.environ['MODAL_PROXY_TOKEN_SECRET']}")

    split = load_split()
    test = split[split["split"].isin(["test", "unseen_test"])].sort_values("path").reset_index(drop=True)
    sub = test.index[test["spark_subset"].astype(bool)].tolist()[:args.limit]
    refs = {f: image_path(pick_refs(split, f)[1]["good"]) for f in test["fabric"].unique()}
    c = pd.read_csv(OUT / "arm_C.csv").set_index("path")  # the locked classifier's probabilities from the test run
    classes = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage"]
    commit = git("rev-parse", "HEAD")

    def call(job):
        arm, i = job
        row = test.iloc[i]
        for attempt in range(3):
            try:
                if arm == "A":
                    res = classify(client, image_path(row.path), size=SIZE, effort=EFFORT, model=MODEL)
                elif arm == "B":
                    res = classify(client, image_path(row.path), size=SIZE, effort=EFFORT, model=MODEL,
                                   refs=[("good", refs[row.fabric])])
                else:
                    probs = c.loc[row.path, [f"p_{k}" for k in classes]].to_numpy(dtype=float)
                    res = hybrid.inspect(client, image_path(row.path), refs[row.fabric], top2(probs), size=SIZE,
                                         effort=EFFORT, model=MODEL)
                return i, res
            except Exception as ex:
                res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
                time.sleep(5 * (attempt + 1))
        return i, res

    base = test[["path", "fabric", "label", "source_class", "split", "spark_subset"]]
    for arm in args.arms:
        with ThreadPoolExecutor(args.workers) as pool:
            out = list(pool.map(call, [(arm, i) for i in sub]))
        df = pd.concat([base.loc[[i for i, _ in out]].reset_index(drop=True), pd.DataFrame([r for _, r in out])], axis=1)
        df = df.assign(commit=commit, model=MODEL, effort=EFFORT)
        name = f"arm_E{arm}" + (f"_first{args.limit}" if args.limit else "")
        df.to_csv(OUT / f"{name}.csv", index=False)
        tok = df[["prompt_tokens", "completion_tokens"]].sum() if "prompt_tokens" in df else None
        print(f"arm E{arm}: {len(df)} images, api errors {(df['pred'] == 'api_error').sum()}, tokens "
              f"{None if tok is None else tok.to_dict()}", flush=True)


if __name__ == "__main__":
    main()
