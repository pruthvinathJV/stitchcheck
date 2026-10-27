"""Measure how many images Muse Spark accepts per request and what each image costs in tokens.

python scripts/smoke/spark_images.py [--counts 1 5] [--big]

Sends n held-out StitchingNet test images (224 px) in one chat request and asks for one label per image.
--big also sends one image upscaled to 1280 px, to estimate the cost of a phone-resolution photo.
Writes results/smoke/spark_images.json.
"""
import argparse
import json
import time
from pathlib import Path

import pandas as pd
from PIL import Image

from stitchcheck.data.stitchingnet import LABELS, image_path, load_split
from stitchcheck.meta_api import SPARK_MODEL, image_data_url, spark_client

PROMPT = ("Each image is a close-up of a sewn seam. For each image, in order, answer with one label from: "
          f"{', '.join(LABELS)}. Reply with only a JSON array of {{n}} strings.")


def run(client, images, prompt):
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": image_data_url(im)}} for im in images]
    t0 = time.time()
    try:
        r = client.chat.completions.create(model=SPARK_MODEL, messages=[{"role": "user", "content": content}])
        text = r.choices[0].message.content
        try:
            parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        except json.JSONDecodeError:
            parsed = None
        return dict(ok=True, seconds=round(time.time() - t0, 2), prompt_tokens=r.usage.prompt_tokens,
                    completion_tokens=r.usage.completion_tokens, answer=text[:500],
                    answers_parsed=isinstance(parsed, list) and len(parsed) == len(images))
    except Exception as e:  # record the failure mode, that is the point of the test
        return dict(ok=False, seconds=round(time.time() - t0, 2), error=f"{type(e).__name__}: {e}"[:500])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", type=int, nargs="+", default=[1, 5])
    ap.add_argument("--big", action="store_true")
    args = ap.parse_args()
    splits = load_split()
    pool = splits[(splits["spark_subset"] == 1) & (splits["split"] == "test")].sample(max(args.counts), random_state=0)
    paths = [image_path(p) for p in pool["path"]]
    client = spark_client()
    runs = []
    for n in args.counts:
        res = run(client, [Image.open(p) for p in paths[:n]], PROMPT.format(n=n))
        runs.append(dict(n_images=n, size=224, **res))
        print(runs[-1])
    if args.big:
        big = Image.open(paths[0]).resize((1280, 1280), Image.BICUBIC)
        runs.append(dict(n_images=1, size=1280, **run(client, [big], PROMPT.format(n=1))))
        print(runs[-1])
    ok = [r for r in runs if r["ok"] and r["size"] == 224]
    per_image = None
    if len(ok) >= 2:
        a, b = ok[0], ok[-1]
        per_image = round((b["prompt_tokens"] - a["prompt_tokens"]) / (b["n_images"] - a["n_images"]), 1)
    out = dict(model=SPARK_MODEL, date=time.strftime("%Y-%m-%d"), runs=runs, tokens_per_224px_image=per_image)
    Path("results/smoke").mkdir(parents=True, exist_ok=True)
    Path("results/smoke/spark_images.json").write_text(json.dumps(out, indent=2))
    print("tokens per 224px image:", per_image)


if __name__ == "__main__":
    main()
