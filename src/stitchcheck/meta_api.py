"""Thin clients for the Meta Model API: Muse Spark (OpenAI-compatible), SAM 3.1, Muse Voice Transcribe.

Reads MODEL_API_KEY from the environment or a local .env file.
"""
import base64
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image

BASE_URL = "https://api.meta.ai/v1"
SPARK_MODEL = "muse-spark-1.3"
SAM_MODEL = "sam-3.1"
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"


def api_key():
    load_dotenv()
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise SystemExit("MODEL_API_KEY is not set (put it in .env; see .env.example)")
    return key


def spark_client():
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=api_key())


def image_data_url(image, max_side=None, fmt="JPEG"):
    """PIL image or path -> base64 data URL, optionally downscaled so the longest side is max_side."""
    im = Image.open(image) if isinstance(image, (str, Path)) else image
    im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format=fmt, quality=92)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"


def sam_segment(image, prompt, include_confidence=True, timeout=120):
    """Segment every instance of a noun-phrase concept. Returns (objects, raw_text).

    objects: list of dict(object_id, box=(x1, y1, x2, y2), confidence, mask=bool array at full image size)
    """
    from meta_sam_parser import CompletedOutcome, image_segmentation_format

    im = Image.open(image) if isinstance(image, (str, Path)) else image
    body = {
        "model": SAM_MODEL,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_data_url(im, fmt="PNG")},
        ]}],
        "stream": False,
    }
    if include_confidence:
        body["metadata"] = {"include_confidence": "true"}
    r = requests.post(f"{BASE_URL}/responses", json=body, timeout=timeout,
                      headers={"Authorization": f"Bearer {api_key()}"})
    r.raise_for_status()
    raw = "".join(c.get("text", "") for item in r.json().get("output", [])
                  for c in item.get("content", []) if c.get("type") == "output_text")

    parser = image_segmentation_format().create_parser()
    parser.push(raw)
    result = parser.finish(CompletedOutcome()).result
    objects = {}
    for rec in result.records:
        if rec.kind == "box":
            objects.setdefault(rec.object_id, {})["box"] = (rec.left, rec.top, rec.right, rec.bottom)
            objects[rec.object_id]["confidence"] = rec.confidence
        elif rec.kind == "mask":
            objects.setdefault(rec.object_id, {})["mask"] = _place_mask(rec, im.size)
    return [dict(object_id=k, **v) for k, v in objects.items()], raw


def _place_mask(rec, image_size):
    from meta_sam_parser import decode_mask_to_raster

    local = np.frombuffer(decode_mask_to_raster(rec.mask), dtype=np.uint8).reshape(rec.mask.height, rec.mask.width)
    full = np.zeros((image_size[1], image_size[0]), dtype=bool)
    x, y = int(rec.bounds.left), int(rec.bounds.top)
    h = min(local.shape[0], full.shape[0] - y)
    w = min(local.shape[1], full.shape[1] - x)
    full[y:y + h, x:x + w] = local[:h, :w] > 0
    return full


def to_wav_16k(audio_path, out_path=None):
    """Convert any audio file to mono 16 kHz 16-bit PCM WAV (the only format the transcribe endpoint takes)."""
    out_path = Path(out_path or tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio_path),
                    "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out_path)], check=True)
    return out_path


def transcribe(wav_path, language_bias=None, keywords=None, timeout=120):
    request = {"model": TRANSCRIBE_MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK"}
    if language_bias:
        request["languageBias"] = list(language_bias)
    if keywords:
        request["keywords"] = list(keywords)
    with open(wav_path, "rb") as f:
        r = requests.post(
            f"{BASE_URL}/asr/transcribe", timeout=timeout,
            headers={"Authorization": f"Bearer {api_key()}", "Accept": "application/json"},
            files={"request": (None, json.dumps(request), "application/json"),
                   "audio": (Path(wav_path).name, f, "audio/wav")},
        )
    r.raise_for_status()
    return r.json()
