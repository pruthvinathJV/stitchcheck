"""Smoke test: Muse Spark Open on Modal's shared endpoint (image input, latency, tokens per call).

python scripts/smoke/spark_open_modal.py [--n 10]

Needs MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET in .env (Modal dashboard, Settings, Proxy tokens).
Lists the models the token can reach, picks the Muse Spark Open endpoint, and sends n Linen validation images
(CC BY, 2 per class, seeded) with the locked zero-shot prompt at 448 px, one image per request, no cache.
Writes results/smoke/spark_open_modal.json. No test-set image is sent.
"""
import argparse
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

from stitchcheck.data.stitchingnet import image_path, load_split
from stitchcheck.spark.classify import classify

BASE_URL = "https://inference.us-west.modal.direct/v1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    load_dotenv()
    token = f"{os.environ['MODAL_PROXY_TOKEN_ID']}.{os.environ['MODAL_PROXY_TOKEN_SECRET']}"
    models = requests.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
    ids = [m["id"] for m in models.get("data", [])]
    print("models:", ids)
    model = next((m for m in ids if "spark" in m.lower() and "open" in m.lower()), None)
    if model is None:
        raise SystemExit("no Muse Spark Open endpoint reachable with this token")

    client = OpenAI(base_url=BASE_URL, api_key=token)
    val = load_split().query("split == 'val'")
    sample = val.groupby("label", group_keys=False).sample(max(1, args.n // 5), random_state=42).head(args.n)
    rows = []
    for row in sample.itertuples():
        t0 = time.time()
        try:
            r = classify(client, image_path(row.path), size=448, effort=None, model=model, cache=False)
        except Exception as ex:
            r = dict(pred="api_error", raw=f"{type(ex).__name__}: {ex}"[:300])
        rows.append(dict(path=row.path, label=row.label, wall_s=round(time.time() - t0, 2), **r))
        print(row.label, "->", r.get("pred"), r.get("seconds"), "s", r.get("prompt_tokens"), "/", r.get("completion_tokens"), flush=True)
    ok = [r for r in rows if r["pred"] != "api_error"]
    out = dict(model=model, base_url=BASE_URL, n=len(rows), errors=len(rows) - len(ok),
               median_s=sorted(r["seconds"] for r in ok)[len(ok) // 2] if ok else None,
               mean_prompt_tokens=sum(r["prompt_tokens"] for r in ok) / len(ok) if ok else None,
               mean_completion_tokens=sum(r["completion_tokens"] for r in ok) / len(ok) if ok else None,
               correct=sum(r["pred"] == r["label"] for r in ok), runs=rows)
    Path("results/smoke/spark_open_modal.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    main()
