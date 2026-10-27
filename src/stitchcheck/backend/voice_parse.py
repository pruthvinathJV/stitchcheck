"""Voice correction -> structured entry: Muse Spark maps a Telugu + English transcript onto fixed lists.

Nothing is saved from voice alone: the proposed entry goes back to the checker, who confirms it with a tap.
Any field Spark cannot match to its list, or marks as unclear, is left out and listed as ambiguous.
"""
import json
import time

from stitchcheck.meta_api import SPARK_MODEL
from stitchcheck.spark.classify import _JSON
from stitchcheck.voice import TELUGU_DEFECT_WORDS

LABELS = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage", "open_seam"]
SEVERITIES = ["critical", "major", "minor", "none"]
FIELDS = ("label", "part", "severity", "operator_id", "machine_id")


def prompt(transcript, line_plan):
    hints = "; ".join(f"{w} = {label}" for label, words in TELUGU_DEFECT_WORDS.items() for w in words)
    return (
        "A garment checker spoke a correction in Telugu and English. Map it onto the fixed lists only.\n"
        f"Transcript: {transcript!r}\n"
        f"label: one of {LABELS}. Telugu words: {hints}.\n"
        f"part: one of {[r['part'] for r in line_plan]}.\n"
        f"severity: one of {SEVERITIES}.\n"
        f"operator_id: one of {[r['operator_id'] for r in line_plan]}; machine_id: one of {[r['machine_id'] for r in line_plan]}. "
        "Spoken numbers map to these IDs (e.g. machine twelve or machine పన్నెండు = M12).\n"
        "Fill only what the checker said. If a word is unclear or matches nothing, leave the field null and add its "
        "name to ambiguous. Never guess.\n"
        'Reply with only JSON: {"label": null, "part": null, "severity": null, "operator_id": null, "machine_id": null, '
        '"ambiguous": []}'
    )


def parse_correction(client, transcript, line_plan, model=SPARK_MODEL, effort="minimal"):
    """Returns dict(proposed={field: value}, ambiguous=[fields], raw, seconds, prompt_tokens, completion_tokens)."""
    t0 = time.time()
    p = prompt(transcript, line_plan)
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
        if v in allowed[f]:
            proposed[f] = v
        elif f not in ambiguous:
            ambiguous.append(f)
    return dict(proposed=proposed, ambiguous=ambiguous, raw=text[:500], prompt=p, seconds=round(time.time() - t0, 2),
                prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens)
