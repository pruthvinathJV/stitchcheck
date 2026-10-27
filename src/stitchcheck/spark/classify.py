"""Muse Spark as a single-image stitch inspector, zero-shot or with labelled reference images (few-shot).

One test image per request, as in the product: no batching, so answers cannot depend on other test images
or their order. Reference images, if any, come first, each with its label. Responses are cached on disk (data/cache/spark/) keyed by everything that affects them.
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
FEW_SHOT_NOTE = (
    "\nBefore the image to inspect you get labelled reference close-ups of seams. They may be on a different fabric "
    "from the image to inspect. Compare the stitch line against them; classify only the last image."
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


def _url(image_path, size):
    im = Image.open(image_path).convert("RGB")
    if max(im.size) != size:
        scale = size / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.BICUBIC)
    return image_data_url(im)


def classify(client, image_path, size=224, effort=None, prompt=ZERO_SHOT_PROMPT, model=SPARK_MODEL, cache=True,
             refs=()):
    """Classify one seam image. size: longest side sent to the API, for the query and references (StitchingNet
    images are 224 px, so larger sizes are upsampled). effort: reasoning_effort, or None for the API default.
    refs: (label, image_path) reference pairs shown before the query; the prompt gets FEW_SHOT_NOTE."""
    url = _url(image_path, size)
    content = [{"type": "text", "text": prompt + (FEW_SHOT_NOTE if refs else "")}]
    for label, ref_path in refs:
        content += [{"type": "text", "text": f"Reference: {label}"},
                    {"type": "image_url", "image_url": {"url": _url(ref_path, size)}}]
    content += ([{"type": "text", "text": "Image to inspect:"}] if refs else []) + [{"type": "image_url", "image_url": {"url": url}}]
    parts = [model, prompt, effort, url] + ([[c.get("text") or c["image_url"]["url"] for c in content]] if refs else [])
    key = hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:32]
    path = CACHE_DIR / f"{key}.json"
    if cache and path.exists():
        return json.loads(path.read_text()) | {"cached": True}

    kwargs = {"reasoning_effort": effort} if effort else {}
    t0 = time.time()
    r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}], **kwargs)
    text = r.choices[0].message.content or ""
    details = getattr(r.usage, "completion_tokens_details", None)
    out = dict(pred=parse_label(text) or "parse_error", raw=text[:500], seconds=round(time.time() - t0, 2),
               prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens,
               reasoning_tokens=getattr(details, "reasoning_tokens", None))
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out | {"cached": False}
