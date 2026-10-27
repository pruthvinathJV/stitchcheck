"""Tech-pack reader: Muse Spark turns a buyer's spec sheet into a draft inspection plan; the supervisor confirms it.

One request per sheet (PDF or photo), strict JSON schema output, then deterministic validation in code: value
ranges, ISO 2859-1 AQL values and what the app supports, ISO 4915 stitch classes, and part names mapped to app parts
only through an explicit synonym table. Nothing the sheet does not say is filled in: blank, TBC or not-given fields
come back as missing. The evaluation is preregistered in docs/techpack_eval.md.
"""
import base64
import io
import json
import re
import time
from pathlib import Path

from PIL import Image, ImageOps

from stitchcheck.meta_api import SPARK_MODEL

EFFORT = None  # the API default, fixed before any sheet was run (one call per sheet; accuracy matters, not cost)
MAX_SIDE = 2048  # photos are sent with this long side
DEFECTS = ["skipped_stitch", "broken_stitch", "crooked_seam", "puckering", "stain_damage", "open_seam", "other"]
SEVERITIES = ["critical", "major", "minor"]
STATUS = ["ok", "missing", "unreadable"]

PROMPT = """You read a garment buyer's tech pack (spec sheet) for a stitching unit's quality inspection.
Extract only what is written on the page. Never infer, complete or correct a value from general knowledge.

Rules:
- For every field give the value, a status and a short source: the words on the page the value came from.
- status "missing" when the field is blank, says TBC, TBD, N/A, "to follow" or similar, or is not on the sheet; the value is then null.
- status "unreadable" when something is written but you cannot read it with confidence; the value is then null.
- A handwritten correction (struck-through print with a new value written by hand) overrides the printed value. Use the handwritten value and list the change under handwritten_changes.
- operations: one entry per row of the stitching or construction table, in page order. part_as_written is the part exactly as printed. stitch_type is the stitch class as written (e.g. 301). spi_target and spi_tolerance are stitches per inch (e.g. "12 +/- 1" gives 12 and 1). seam_allowance_mm in millimetres. critical is true or false only if the sheet marks it; otherwise null.
- aql: the inspection level and the AQL values for critical, major and minor defects, as written.
- severity_rules: every buyer-specific defect classification rule. text_as_written exactly as printed (any language); meaning_en is an English translation. defect is one of the listed labels, or "other" if none fits. severity is the class the rule assigns.
- general_notes: any other instruction to the line (any language), with an English translation.
- out_of_scope: names of sections that are not about stitching quality or AQL inspection (for example measurements or packing).
"""

_FIELD = {"type": "object", "additionalProperties": False, "required": ["value", "status", "source"],
          "properties": {"value": {"type": ["string", "null"]}, "status": {"type": "string", "enum": STATUS},
                         "source": {"type": "string"}}}
_NUM = {"type": "object", "additionalProperties": False, "required": ["value", "status", "source"],
        "properties": {"value": {"type": ["number", "null"]}, "status": {"type": "string", "enum": STATUS},
                       "source": {"type": "string"}}}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["header", "operations", "aql", "severity_rules", "general_notes", "handwritten_changes", "out_of_scope"],
    "properties": {
        "header": {"type": "object", "additionalProperties": False,
                   "required": ["buyer", "style_no", "style_name", "fabric", "color", "thread"],
                   "properties": {k: _FIELD for k in ["buyer", "style_no", "style_name", "fabric", "color", "thread"]}},
        "operations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["part_as_written", "operation", "stitch_type", "spi_target", "spi_tolerance", "seam_allowance_mm",
                         "critical", "status", "source"],
            "properties": {"part_as_written": {"type": "string"}, "operation": {"type": "string"},
                           "stitch_type": {"type": ["string", "null"]}, "spi_target": {"type": ["number", "null"]},
                           "spi_tolerance": {"type": ["number", "null"]}, "seam_allowance_mm": {"type": ["number", "null"]},
                           "critical": {"type": ["boolean", "null"]}, "status": {"type": "string", "enum": STATUS},
                           "source": {"type": "string"}}}},
        "aql": {"type": "object", "additionalProperties": False, "required": ["inspection_level", "critical", "major", "minor"],
                "properties": {"inspection_level": _FIELD, "critical": _NUM, "major": _NUM, "minor": _NUM}},
        "severity_rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text_as_written", "meaning_en", "defect", "location", "severity", "source"],
            "properties": {"text_as_written": {"type": "string"}, "meaning_en": {"type": "string"},
                           "defect": {"type": "string", "enum": DEFECTS}, "location": {"type": "string"},
                           "severity": {"type": "string", "enum": SEVERITIES}, "source": {"type": "string"}}}},
        "general_notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text_as_written", "meaning_en"],
            "properties": {"text_as_written": {"type": "string"}, "meaning_en": {"type": "string"}}}},
        "handwritten_changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["field", "printed", "written", "source"],
            "properties": {"field": {"type": "string"}, "printed": {"type": "string"}, "written": {"type": "string"},
                           "source": {"type": "string"}}}},
        "out_of_scope": {"type": "array", "items": {"type": "string"}},
    },
}

# Sheet part names -> app parts. Exact match after normalisation only; anything else is unmapped, never the nearest.
PART_SYNONYMS = {
    "collar": "collar", "collar band": "collar", "collar stand": "collar",
    "placket": "placket", "front placket": "placket",
    "pocket": "pocket", "chest pocket": "pocket", "flap pocket": "pocket",
    "sleeve": "sleeve", "armhole": "sleeve", "sleeve attach": "sleeve", "armhole sleeve attach": "sleeve",
    "side seam": "side_seam",
    "cuff": "cuff", "cuffs": "cuff",
    "hem": "hem", "bottom hem": "hem",
    "shoulder": "shoulder", "shoulder seam": "shoulder",
}
# ISO 2859-1 preferred AQL values; the app supports only the ones it has verified tables for.
ISO_AQL = [0.010, 0.015, 0.025, 0.040, 0.065, 0.10, 0.15, 0.25, 0.40, 0.65, 1.0, 1.5, 2.5, 4.0, 6.5, 10, 15, 25, 40, 65,
           100, 150, 250, 400, 650, 1000]
SUPPORTED_AQL = {"critical": [0], "major": [2.5], "minor": [4.0]}
LEVELS = ["I", "II", "III", "S-1", "S-2", "S-3", "S-4"]
SUPPORTED_LEVELS = ["II"]
RANGES = {"spi_target": (6, 20), "spi_tolerance": (0, 3), "seam_allowance_mm": (1, 30)}
STITCH_CLASS = re.compile(r"^[1-6]\d\d$")


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text).lower())).strip()


def map_part(part_as_written):
    """App part for a sheet's part name, or None (unmapped)."""
    return PART_SYNONYMS.get(norm(part_as_written))


def _level(text):
    t = norm(text).upper().replace("LEVEL", "").replace("GENERAL", "").replace("INSPECTION", "").strip()
    t = t.replace("S ", "S-")
    return t if t in LEVELS else None


def validate(plan):
    """Checks in code after extraction. Returns dict(errors, unsupported, unmapped_parts, missing, warnings)."""
    out = dict(errors=[], unsupported=[], unmapped_parts=[], missing=[], warnings=[])
    for k, f in plan["header"].items():
        if f["status"] != "ok" or f["value"] is None:
            out["missing"].append(f"header.{k} ({f['status']})")
    for i, op in enumerate(plan["operations"], 1):
        app_part = map_part(op["part_as_written"])
        op["app_part"] = app_part
        if app_part is None:
            out["unmapped_parts"].append(op["part_as_written"])
        for k, (lo, hi) in RANGES.items():
            v = op[k]
            if v is not None and not lo <= v <= hi:
                out["errors"].append(f"operation {i} {k}={v} outside {lo} to {hi}")
        if op["stitch_type"] is not None and not STITCH_CLASS.match(str(op["stitch_type"]).strip()):
            out["errors"].append(f"operation {i} stitch_type {op['stitch_type']!r} is not an ISO 4915 class")
        if not op["source"].strip():
            out["warnings"].append(f"operation {i} has no source")
    aql = plan["aql"]
    level = aql["inspection_level"]
    if level["value"] is not None:
        lv = _level(level["value"])
        if lv is None:
            out["errors"].append(f"inspection level {level['value']!r} is not an ISO 2859-1 level")
        elif lv not in SUPPORTED_LEVELS:
            out["unsupported"].append(f"aql.inspection_level={lv} (only II is supported)")
    for sev in ("critical", "major", "minor"):
        v = aql[sev]["value"]
        if v is None:
            out["missing"].append(f"aql.{sev} ({aql[sev]['status']})")
        elif v not in ISO_AQL and v != 0:
            out["errors"].append(f"aql.{sev}={v} is not an ISO 2859-1 AQL value")
        elif v not in SUPPORTED_AQL[sev]:
            out["unsupported"].append(f"aql.{sev}={v} (only {SUPPORTED_AQL[sev][0]} is supported)")
    return out


def _content(path):
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        data = base64.b64encode(path.read_bytes()).decode()
        return {"type": "file", "file": {"filename": path.name, "file_data": f"data:application/pdf;base64,{data}"}}
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"}}


def extract(client, path, model=SPARK_MODEL, effort=EFFORT):
    """One sheet -> dict(plan, validation, raw, seconds, prompt_tokens, completion_tokens)."""
    t0 = time.time()
    kwargs = {"reasoning_effort": effort} if effort else {}
    r = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": [{"type": "text", "text": PROMPT}, _content(path)]}],
        response_format={"type": "json_schema", "json_schema": {"name": "inspection_plan", "schema": SCHEMA, "strict": True}},
        **kwargs)
    raw = r.choices[0].message.content or ""
    plan = json.loads(raw)
    return dict(plan=plan, validation=validate(plan), raw=raw, seconds=round(time.time() - t0, 2),
                prompt_tokens=r.usage.prompt_tokens, completion_tokens=r.usage.completion_tokens)
