"""Telugu-English code-switched transcription check.

python scripts/smoke/transcribe_telugu.py CLIP [--reference "what was actually said"]

Converts CLIP (any format ffmpeg reads, e.g. a WhatsApp .opus or .m4a) to 16 kHz mono WAV and transcribes it
three ways: no language hint, languageBias=[Telugu, English], and the same plus QC keywords.
Writes results/smoke/transcribe_<clip name>.json. The audio itself is never committed.
"""
import argparse
import json
import time
from pathlib import Path

from stitchcheck.meta_api import TRANSCRIBE_MODEL, to_wav_16k, transcribe

KEYWORDS = ["machine", "bundle", "operator", "collar", "pocket", "hem", "seam", "skipped stitch",
            "broken stitch", "puckering", "crooked seam", "stain", "open seam", "major", "minor", "defect",
            "agree", "reject"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("--reference", default="")
    args = ap.parse_args()
    Path("data/audio").mkdir(parents=True, exist_ok=True)
    wav = to_wav_16k(args.clip, Path("data/audio") / f"{args.clip.stem}_16k.wav")
    variants = {
        "auto": dict(),
        "bias": dict(language_bias=["Telugu", "English"]),
        "bias+keywords": dict(language_bias=["Telugu", "English"], keywords=KEYWORDS),
    }
    runs = []
    for name, kwargs in variants.items():
        t0 = time.time()
        try:
            r = transcribe(wav, **kwargs)
            runs.append(dict(variant=name, ok=True, seconds=round(time.time() - t0, 2),
                             transcript=r.get("transcript"), audio_ms=r.get("audioDurationMs")))
        except Exception as e:
            runs.append(dict(variant=name, ok=False, error=f"{type(e).__name__}: {e}"[:500]))
        print(runs[-1])
    Path("results/smoke").mkdir(parents=True, exist_ok=True)
    Path(f"results/smoke/transcribe_{args.clip.stem}.json").write_text(json.dumps(
        dict(model=TRANSCRIBE_MODEL, date=time.strftime("%Y-%m-%d"), reference=args.reference, runs=runs),
        indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
