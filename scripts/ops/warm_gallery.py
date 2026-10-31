"""Run every demo gallery photo through the live pipeline once, so visitors' taps are served from the caches.

python scripts/ops/warm_gallery.py [URL] [--passes 2]

Uses a private demo copy. Pass 1 fills the SAM and Muse Spark caches on the volume; later passes show the cached
timing a visitor gets. Prints, per photo and pass: step-1 seconds, seconds until the verdict, the label and brain.
"""
import argparse
import time

import requests

DEFAULT_URL = "https://jvpnath--stitchcheck-web-web.us-east.modal.run"


def shot(http, url, item, garment_id):
    t0 = time.time()
    r = http.post(f"{url}/demo/gallery/{item['id']}", json={"garment_id": garment_id, "part": item["part"]}, timeout=120)
    r.raise_for_status()
    s1 = r.json()
    if "retake" in s1:
        return dict(step1=round(time.time() - t0, 1), verdict=None, label="retake", brain=None)
    step1 = round(time.time() - t0, 1)
    while True:
        insp = http.get(f"{url}/inspections/{s1['inspection_id']}", timeout=60).json()
        if insp["review_status"] == "done" or time.time() - t0 > 120:
            break
        time.sleep(0.5)
    return dict(step1=step1, verdict=round(time.time() - t0, 1), label=insp.get("proposed_label"),
                brain=(insp.get("spark_verdict") or {}).get("brain"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=DEFAULT_URL)
    ap.add_argument("--passes", type=int, default=2)
    args = ap.parse_args()
    http = requests.Session()  # keeps the visitor cookie
    demo = http.post(f"{args.url}/demo/session", timeout=120).json()
    items = http.get(f"{args.url}/demo/gallery", timeout=60).json()
    print(f"{len(items)} gallery photos, demo session {demo['session_id']}")
    for p in range(1, args.passes + 1):
        g = http.post(f"{args.url}/bundles/{demo['bundle_id']}/garments", json={"garment_no": 900 + p}, timeout=60).json()
        for item in items:
            r = shot(http, args.url, item, g["id"])
            print(f"pass {p}  {item['id']:<28} step1 {r['step1']:>5}s  verdict {r['verdict']}s  {r['label']} ({r['brain']})",
                  flush=True)


if __name__ == "__main__":
    main()
