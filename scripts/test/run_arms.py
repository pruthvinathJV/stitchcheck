"""Run arms A, B, C and D on the StitchingNet test fabrics, once, from the locked commit.

python scripts/test/run_arms.py --write-lock   write results/test/lock.json (before the lock commit and tag)
python scripts/test/run_arms.py                run all arms; refuses unless the tree is clean, HEAD is the commit
                                               tagged test-lock, and lock.json matches what would run now

A: zero-shot Muse Spark. B: Muse Spark with one good reference. C: the 3-seed 8-fabric classifier ensemble
(full test split and unseen-defect test). D: inspect(), the hybrid the app runs. A, B and D run on the Spark subset.
Every setting comes from docs/design.md. Writes raw predictions to results/test/arm_{A,B,C,D}.csv; metrics are
computed from those files afterwards, so the predictions cannot change.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "val"))
from spark_fewshot import SEED, SIZE, pick_refs  # noqa: E402

from stitchcheck.classifier.ensemble import BENCH_RUNS, ensemble_probs, top2  # noqa: E402
from stitchcheck.classifier.train import CLASSES  # noqa: E402
from stitchcheck.data.stitchingnet import SPLIT_FILE, image_path, load_split  # noqa: E402
from stitchcheck.meta_api import SPARK_MODEL, spark_client  # noqa: E402
from stitchcheck.spark import inspect as hybrid  # noqa: E402
from stitchcheck.spark.classify import FEW_SHOT_NOTE, ZERO_SHOT_PROMPT, classify  # noqa: E402

OUT = Path("results/test")
LOCK = OUT / "lock.json"
EFFORT = "minimal"
TAG = "test-lock"


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()[:16]


def lock_contents():
    split = load_split()
    test = split[split["split"].isin(["test", "unseen_test"])]
    refs = {f: pick_refs(split, f)[1]["good"] for f in sorted(test["fabric"].unique())}
    return dict(
        spark_model=SPARK_MODEL, size=SIZE, effort=EFFORT, seed=SEED,
        prompts={"zero_shot": sha(ZERO_SHOT_PROMPT), "few_shot_note": sha(FEW_SHOT_NOTE), "inspect": sha(hybrid.INSPECT_PROMPT)},
        few_shot_design="one good reference (a)", references=refs,
        hybrid=dict(protect_t=hybrid.PROTECT_T, major=list(hybrid.MAJOR), no_downgrade=list(hybrid.NO_DOWNGRADE)),
        classifier_runs={r: sha(Path("models", r, "best.pt").read_bytes()) for r in BENCH_RUNS},
        split_file=str(SPLIT_FILE), split_sha=sha(SPLIT_FILE.read_bytes()),
        n_test=int((test["split"] == "test").sum()), n_unseen=int((test["split"] == "unseen_test").sum()),
        n_spark_subset=int(test["spark_subset"].sum()),
        metrics="docs/design.md, Metrics; src/stitchcheck/eval/metrics.py", bootstrap=dict(n_boot=2000, seed=0),
    )


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def check_lock():
    if git("status", "--porcelain"):
        raise SystemExit("working tree has changes: commit or stash them; the test runs only from the locked commit")
    head, tagged = git("rev-parse", "HEAD"), git("rev-parse", f"{TAG}^{{commit}}")
    if head != tagged:
        raise SystemExit(f"HEAD {head[:8]} is not the {TAG} commit {tagged[:8]}")
    if json.loads(LOCK.read_text()) != lock_contents():
        raise SystemExit("lock.json does not match the current settings, prompts or weights")
    return head


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-lock", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if args.write_lock:
        OUT.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(json.dumps(lock_contents(), indent=2))
        print(LOCK.read_text())
        return
    commit = check_lock()

    split = load_split()
    test = split[split["split"].isin(["test", "unseen_test"])].sort_values("path").reset_index(drop=True)
    base = test[["path", "fabric", "label", "source_class", "split", "spark_subset"]]

    probs = ensemble_probs(test)
    classes = np.array(CLASSES["clean"])
    arm_c = base.assign(pred=classes[probs.argmax(1)], conf=probs.max(1),
                        **{f"p_{c}": probs[:, i] for i, c in enumerate(classes)}, commit=commit)
    arm_c.to_csv(OUT / "arm_C.csv", index=False)
    print(f"arm C: {len(arm_c)} images", flush=True)

    sub = test.index[test["spark_subset"]].tolist()
    refs = {f: image_path(pick_refs(split, f)[1]["good"]) for f in test["fabric"].unique()}
    client = spark_client()

    def spark(job):
        arm, i = job
        row = test.iloc[i]
        for attempt in range(3):  # transient API errors only; a successful reply is never re-asked
            try:
                if arm == "A":
                    res = classify(client, image_path(row.path), size=SIZE, effort=EFFORT)
                elif arm == "B":
                    res = classify(client, image_path(row.path), size=SIZE, effort=EFFORT, refs=[("good", refs[row.fabric])])
                else:
                    res = hybrid.inspect(client, image_path(row.path), refs[row.fabric], top2(probs[i]), size=SIZE, effort=EFFORT)
                return arm, i, res
            except Exception as ex:
                res = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
                time.sleep(5 * (attempt + 1))
        return arm, i, res

    for arm in ("A", "B", "D"):
        with ThreadPoolExecutor(args.workers) as pool:
            out = list(pool.map(spark, [(arm, i) for i in sub]))
        df = pd.concat([base.loc[[i for _, i, _ in out]].reset_index(drop=True),
                        pd.DataFrame([r for _, _, r in out])], axis=1).assign(commit=commit)
        df.to_csv(OUT / f"arm_{arm}.csv", index=False)
        print(f"arm {arm}: {len(df)} images, api errors {(df['pred'] == 'api_error').sum()}", flush=True)


if __name__ == "__main__":
    main()
