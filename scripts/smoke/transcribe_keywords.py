"""Do Telugu-script keywords help or hurt transcription? Repeats each setting a few times per clip.

python scripts/smoke/transcribe_keywords.py CLIP [CLIP ...] [--repeats 3] [--expect జారాయి]

Compares the English keyword list with the same list plus the Telugu defect words. Counts how often the
expected defect word comes back exactly. Writes results/smoke/transcribe_keywords.csv.
"""
import argparse
from pathlib import Path

import pandas as pd

from stitchcheck.meta_api import to_wav_16k, transcribe
from stitchcheck.voice import KEYWORDS, LANGUAGE_BIAS, TELUGU_DEFECT_WORDS

TELUGU_WORDS = [w for words in TELUGU_DEFECT_WORDS.values() for w in words]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--expect", default="జారాయి")
    args = ap.parse_args()
    rows = []
    for clip in args.clips:
        wav = to_wav_16k(clip, Path("data/audio") / f"{clip.stem}_16k.wav")
        for name, keywords in [("english", KEYWORDS), ("english+telugu", KEYWORDS + TELUGU_WORDS)]:
            for i in range(args.repeats):
                text = transcribe(wav, language_bias=LANGUAGE_BIAS, keywords=keywords).get("transcript", "").strip()
                rows.append(dict(clip=clip.stem, keywords=name, run=i, expected_word_ok=args.expect in text,
                                 transcript=text))
                print(rows[-1])
    df = pd.DataFrame(rows)
    Path("results/smoke").mkdir(parents=True, exist_ok=True)
    df.to_csv("results/smoke/transcribe_keywords.csv", index=False)
    print(df.groupby(["keywords", "clip"])["expected_word_ok"].agg(["sum", "count"]))


if __name__ == "__main__":
    main()
