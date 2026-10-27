"""Voice correction -> structured entry: Muse Spark maps a Telugu + English transcript onto fixed lists.

Nothing is saved from voice alone: the proposed entry goes back to the checker, who confirms it with a tap.
Any field Spark cannot match to its list, or marks as unclear, is left out and listed as ambiguous; for an unclear
defect Spark also returns its 2 or 3 most likely labels, shown as one-tap buttons. Number words (Telugu or English,
1 to 99: machines 1 to 25, bundle numbers) are turned into digits in code before Spark sees the transcript.
"""
import json
import re
import time

from stitchcheck.meta_api import SPARK_MODEL
from stitchcheck.spark.classify import _JSON
from stitchcheck.voice import TELUGU_DEFECT_WORDS

LABELS = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage", "open_seam"]
SEVERITIES = ["critical", "major", "minor", "none"]
FIELDS = ("label", "part", "severity", "operator_id", "machine_id")

TELUGU_UNITS = ["ఒకటి", "రెండు", "మూడు", "నాలుగు", "ఐదు", "ఆరు", "ఏడు", "ఎనిమిది", "తొమ్మిది"]
TELUGU_TEENS = ["పది", "పదకొండు", "పన్నెండు", "పదమూడు", "పద్నాలుగు", "పదిహేను", "పదహారు", "పదిహేడు", "పద్దెనిమిది",
                "పందొమ్మిది"]
ENGLISH_UNITS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
ENGLISH_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                 "nineteen"]
TELUGU_TENS = ["ఇరవై", "ముప్పై", "నలభై", "యాభై", "అరవై", "డెబ్బై", "ఎనభై", "తొంభై"]  # 20 to 90
ENGLISH_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _number_words():
    words = {}
    for i, w in enumerate(TELUGU_UNITS + TELUGU_TEENS, 1):
        words[w] = i
    for i, w in enumerate(ENGLISH_UNITS + ENGLISH_TEENS, 1):
        words[w] = i
    for tens, (tt, et) in enumerate(zip(TELUGU_TENS, ENGLISH_TENS), 2):
        words[tt] = words[et] = 10 * tens
        for i, (tu, eu) in enumerate(zip(TELUGU_UNITS, ENGLISH_UNITS), 1):
            words[f"{tt} {tu}"] = words[f"{et} {eu}"] = words[f"{et}-{eu}"] = 10 * tens + i
    return dict(sorted(words.items(), key=lambda kv: -len(kv[0])))  # longest first, so "twenty one" beats "one"


NUMBER_WORDS = _number_words()
_NUMBER_RE = re.compile("|".join(f"(?<!\\S){re.escape(w)}(?!\\S)" for w in NUMBER_WORDS), re.IGNORECASE)


def normalise_numbers(text):
    """Number words from 1 to 99, Telugu or English, to digits: "machine ఏడు" -> "machine 7"."""
    return _NUMBER_RE.sub(lambda m: str(NUMBER_WORDS[m.group(0).lower()]), text)


def prompt(transcript, line_plan):
    hints = "; ".join(f"{w} = {label}" for label, words in TELUGU_DEFECT_WORDS.items() for w in words)
    return (
        "A garment checker spoke a correction in Telugu and English. Map it onto the fixed lists only.\n"
        f"Transcript: {transcript!r}\n"
        f"label: one of {LABELS}. Telugu words: {hints}.\n"
        f"part: one of {[r['part'] for r in line_plan]}.\n"
        f"severity: one of {SEVERITIES}.\n"
        f"operator_id: one of {[r['operator_id'] for r in line_plan]}; machine_id: one of {[r['machine_id'] for r in line_plan]}. "
        "Spoken numbers are already written as digits; a machine number maps to the machine_id with that number.\n"
        "Fill only what the checker said. If a word is unclear or matches nothing, leave the field null and add its "
        "name to ambiguous. Never guess. If the defect word is unclear, put the 2 or 3 most likely labels in "
        "label_candidates, most likely first.\n"
        'Reply with only JSON: {"label": null, "part": null, "severity": null, "operator_id": null, "machine_id": null, '
        '"ambiguous": [], "label_candidates": []}'
    )


def parse_correction(client, transcript, line_plan, model=SPARK_MODEL, effort="minimal"):
    """Returns dict(proposed={field: value}, ambiguous=[fields], label_candidates, raw, prompt, seconds, tokens)."""
    t0 = time.time()
    p = prompt(normalise_numbers(transcript), line_plan)
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": p}], reasoning_effort=effort)
    text = r.choices[0].message.content or ""
    m = _JSON.search(text)
    try:
        out = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        out = {}
    allowed = dict(label=LABELS, part=[x["part"] for x in line_plan], severity=SEVERITIES,
                   operator_id=[x["operator_id"] for x in line_plan], machine_id=[x["machine_id"] for x in line_plan])
    proposed, ambiguous = {}, [f for f in out.get("ambiguous", []) if f in FIELDS]
    for f in FIELDS:
        v = out.get(f)
        if v is None:
            continue
        v = str(v) if f in ("operator_id", "machine_id") else v
        if v in allowed[f]:
            proposed[f] = v
        elif f not in ambiguous:
            ambiguous.append(f)
    candidates = []
    if "label" not in proposed:
        candidates = [c for c in dict.fromkeys(out.get("label_candidates") or []) if c in LABELS][:3]
        if candidates and "label" not in ambiguous:
            ambiguous.append("label")
    return dict(proposed=proposed, ambiguous=ambiguous, label_candidates=candidates, raw=text[:500], prompt=p, seconds=round(time.time() - t0, 2),
                prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens)
