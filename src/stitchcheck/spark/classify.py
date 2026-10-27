"""Muse Spark as a single-image stitch inspector (zero-shot).

One test image per request, as in the product: no batching, so answers cannot depend on other images
or their order. Responses are cached on disk (data/cache/spark/) keyed by everything that affects them.
"""
import hashlib
import json
import re
import time
from pathlib import Path

from PIL import Image

from stitchcheck.meta_api import SPARK_MODEL, image_data_url

SPARK_LABELS = ["good", "skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage",
                "unknown_defect"]
ZERO_SHOT_PROMPT = (
    "You are a garment quality inspector. The image is a close-up of a sewn seam.\n"
    "Classify it with exactly one label:\n"
    "- good: even, continuous stitches, no defect\n"
    "- skipped_stitch: one or more stitches missing, longer gaps in the stitch line\n"
    "- broken_stitch: thread cut or broken, loose ends\n"
    "- crooked_seam: stitch line wanders or is not straight\n"
    "- puckering: fabric gathered or wrinkled along the seam\n"
    "- stain_damage: stain, dirt, hole or other damage on the fabric near the seam\n"
    "- unknown_defect: a defect that fits none of the above\n"
    'Reply with only JSON: {"label": "<label>", "reason": "<one short sentence>"}'
)
CACHE_DIR = Path("data/cache/spark")
_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_label(text):
    """Return a label from SPARK_LABELS, or None if the reply can't be read."""
    m = _JSON.search(text or "")
    if m:
        try:
            label = str(json.loads(m.group(0)).get("label", "")).strip().lower()
            return label if label in SPARK_LABELS else None
        except json.JSONDecodeError:
            pass
    words = re.findall(r"[a-z_]+", (text or "").lower())
    hits = [w for w in words if w in SPARK_LABELS]
    return hits[0] if len(set(hits)) == 1 else None


def classify(client, image_path, size=224, effort=None, prompt=ZERO_SHOT_PROMPT, model=SPARK_MODEL, cache=True):
    """Classify one seam image. size: longest side sent to the API (StitchingNet images are 224 px, so
    larger sizes are upsampled). effort: reasoning_effort, or None for the API default."""
    im = Image.open(image_path).convert("RGB")
    if max(im.size) != size:
        scale = size / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.BICUBIC)
    url = image_data_url(im)
    key = hashlib.sha256(json.dumps([model, prompt, effort, url]).encode()).hexdigest()[:32]
    path = CACHE_DIR / f"{key}.json"
    if cache and path.exists():
        return json.loads(path.read_text()) | {"cached": True}

    kwargs = {"reasoning_effort": effort} if effort else {}
    t0 = time.time()
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}], **kwargs)
    text = r.choices[0].message.content or ""
    details = getattr(r.usage, "completion_tokens_details", None)
    out = dict(pred=parse_label(text) or "parse_error", raw=text[:500], seconds=round(time.time() - t0, 2),
               prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens,
               reasoning_tokens=getattr(details, "reasoning_tokens", None))
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out | {"cached": False}
