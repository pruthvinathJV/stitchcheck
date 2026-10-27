"""The hybrid inspector: Muse Spark with the classifier as a tool. Shared by benchmark arm D and the app.

One image to inspect per request, with one good-seam reference (few-shot design (a)). Spark is told to call
classify_stitch, which returns the classifier's top-2 labels and probabilities, and then gives a JSON verdict.
The API only accepts tool_choice "auto", so a reply without the tool call is retried once; if Spark still skips
the tool, the photo goes to the checker with the classifier's label. The hybrid rule (docs/design.md) is applied
in code after the call. Responses are cached on disk like classify.py, keyed by everything that affects them.
"""
import hashlib
import json
import time

from stitchcheck.meta_api import SPARK_MODEL
from stitchcheck.spark.classify import CACHE_DIR, FEW_SHOT_NOTE, ZERO_SHOT_PROMPT, _JSON, _url

ASK_CHECKER = "ask_checker"
MAJOR = ("skipped_stitch", "broken_stitch", "stain_damage")
NO_DOWNGRADE = ("good", "crooked_seam")  # a protected major call can't become these
PROTECT_T = 0.60  # chosen on Linen, classifier only; see docs/design.md
LABELS = ("good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage", "open_seam",
          "unknown_defect")
SEVERITY = {"skipped_stitch": "major", "broken_stitch": "major", "open_seam": "major", "good": "none"}
SPARK_JUDGES_SEVERITY = ("stain_damage", "crooked_seam", "puckering")

INSPECT_PROMPT = ZERO_SHOT_PROMPT.split("Reply with only JSON")[0] + (
    "- open_seam: the two fabric pieces have come apart along the seam\n"
    "Before answering, call classify_stitch once: it runs a stitch classifier trained on these labels (except "
    "puckering, open_seam and unknown_defect) and returns its top-2 labels with probabilities. Use it as evidence, "
    "not as the answer.\n"
    'Then reply with only JSON: {"verdict": "confirm|override|unknown_defect|ask_checker", "label": "<label>", '
    '"severity_judgement": "major|minor|null", "reason": "<one short sentence a checker understands>", '
    '"confidence": <0 to 1>}. verdict: confirm if you agree with the classifier\'s top label, override if you '
    "choose a different label, unknown_defect for a defect that fits no label, ask_checker if you cannot tell. "
    "severity_judgement only for stain_damage (major if visible on the face side), crooked_seam and puckering "
    "(major or minor by extent and visibility); otherwise null."
) + FEW_SHOT_NOTE
TOOLS = [{"type": "function", "function": {
    "name": "classify_stitch",
    "description": "Run the fine-tuned stitch classifier on the image to inspect. Returns its top-2 labels with "
                   "probabilities. Call it once before giving a verdict.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]


def parse_verdict(text):
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    label = str(v.get("label", "")).strip().lower()
    label = "unknown_defect" if label == "other" or v.get("verdict") == "unknown_defect" else label
    return v | {"label": label} if label in LABELS else None


def hybrid_rule(top2, verdict, t=PROTECT_T):
    """Final routing. Returns (pred, proposed, protected): pred is the label used for scoring, or ask_checker;
    proposed is the label pre-filled for the checker."""
    (c_label, c_prob), = top2[:1]
    protected = c_label in MAJOR and c_prob >= t
    if verdict is None:
        return ASK_CHECKER, c_label, protected
    s_label = verdict["label"]
    keep_classifier = protected and s_label in NO_DOWNGRADE
    proposed = c_label if keep_classifier else s_label
    if verdict.get("verdict") == ASK_CHECKER or s_label != c_label:
        return ASK_CHECKER, proposed, protected
    return s_label, proposed, protected


def severity(label, verdict):
    if label in SPARK_JUDGES_SEVERITY:
        judged = (verdict or {}).get("severity_judgement")
        return judged if judged in ("major", "minor") else "major"  # unjudged: the cautious call
    return SEVERITY.get(label, "major")


def inspect(client, image_path, ref_path, top2, size=448, effort="minimal", model=SPARK_MODEL, cache=True):
    """top2: [(label, prob), (label, prob)] from the classifier ensemble on this image."""
    url, ref_url = _url(image_path, size), _url(ref_path, size)
    tool_out = json.dumps({"top2": [[lab, round(float(p), 3)] for lab, p in top2]})
    key = hashlib.sha256(json.dumps(["inspect", model, INSPECT_PROMPT, effort, url, ref_url, tool_out]).encode()).hexdigest()[:32]
    path = CACHE_DIR / f"{key}.json"
    if cache and path.exists():
        spark = json.loads(path.read_text()) | {"cached": True}
    else:
        spark = _call(client, url, ref_url, tool_out, effort, model)
        if cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(spark))
        spark |= {"cached": False}
    verdict = parse_verdict(spark["raw"]) if spark["tool_called"] else None
    pred, proposed, protected = hybrid_rule(top2, verdict)
    return dict(pred=pred, proposed=proposed, protected=protected, severity=severity(proposed, verdict),
                spark_label=(verdict or {}).get("label"), spark_verdict=(verdict or {}).get("verdict"),
                reason=(verdict or {}).get("reason"), confidence=(verdict or {}).get("confidence"),
                classifier_top2=top2, **spark)


def _call(client, url, ref_url, tool_out, effort, model):
    content = [{"type": "text", "text": INSPECT_PROMPT},
               {"type": "text", "text": "Reference: good"}, {"type": "image_url", "image_url": {"url": ref_url}},
               {"type": "text", "text": "Image to inspect:"}, {"type": "image_url", "image_url": {"url": url}}]
    kwargs = {"reasoning_effort": effort} if effort else {}
    t0, tokens_in, tokens_out = time.time(), 0, 0
    for attempt in (1, 2):
        msgs = [{"role": "user", "content": content}]
        r1 = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS, **kwargs)
        tokens_in, tokens_out = tokens_in + r1.usage.prompt_tokens, tokens_out + r1.usage.completion_tokens
        m = r1.choices[0].message
        if m.tool_calls:
            break
    else:
        return dict(raw=(m.content or "")[:500], tool_called=False, attempts=2, seconds=round(time.time() - t0, 2),
                    prompt_tokens=tokens_in, completion_tokens=tokens_out)
    msgs += [m.model_dump(exclude_none=True), {"role": "tool", "tool_call_id": m.tool_calls[0].id, "content": tool_out}]
    r2 = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS, **kwargs)
    return dict(raw=(r2.choices[0].message.content or "")[:500], tool_called=True, attempts=attempt,
                seconds=round(time.time() - t0, 2), prompt_tokens=tokens_in + r2.usage.prompt_tokens,
                completion_tokens=tokens_out + r2.usage.completion_tokens)
