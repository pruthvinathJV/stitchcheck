"""Telugu voice corrections: transcription settings shared by the smoke test and the app backend.

Every call sends the Telugu + English language hint and the QC keyword list. Without the hint, a noisy
clip came back partly in Kannada script; without keywords, "major" was heard as "manager".
Keywords are English only: adding the Telugu defect words as keywords made the defect word itself come
back wrong (4 of 12 runs right vs 10 of 12; scripts/smoke/transcribe_keywords.py). The Telugu words are
given to Muse Spark instead, when it maps the transcript to a defect label.
"""
from stitchcheck.meta_api import to_wav_16k, transcribe

LANGUAGE_BIAS = ["Telugu", "English"]

# Telugu defect words, keyed by the fixed defect label they usually mean. For the Spark mapping prompt,
# not for transcription keywords (see above).
TELUGU_DEFECT_WORDS = {
    "skipped_stitch": ["జారాయి"],
    "broken_stitch": ["తెగింది"],
    "crooked_seam": ["వంకర"],
    "puckering": ["ముడతలు"],
    "stain_damage": ["మరక"],
}
GARMENT_PARTS = ["collar", "pocket", "hem", "cuff", "sleeve", "placket", "side seam", "shoulder", "button"]
KEYWORDS = (
    ["machine", "bundle", "operator", "major", "minor", "defect", "agree", "reject",
     "seam", "skipped stitch", "broken stitch", "crooked seam", "puckering", "stain", "open seam"]
    + GARMENT_PARTS
)


def transcribe_correction(audio_path):
    """Any audio file -> transcript text, always with the language hint and keywords."""
    wav = to_wav_16k(audio_path)
    return transcribe(wav, language_bias=LANGUAGE_BIAS, keywords=KEYWORDS).get("transcript", "").strip()
