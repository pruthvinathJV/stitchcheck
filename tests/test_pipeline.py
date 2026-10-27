import io
import json

import numpy as np
from PIL import Image

from stitchcheck.backend.pipeline import inspect_photo, localize, seam_tiles

PLAN = [dict(part="collar", operator_id="OP01", machine_id="M01"), dict(part="pocket", operator_id="OP06", machine_id="M06")]


def photo_bytes(w=800, h=600):
    im = Image.new("RGB", (w, h), (200, 200, 210))
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    return buf.getvalue()


def line_mask(w=800, h=600, angle_deg=0.0, thickness=4):
    yy, xx = np.mgrid[0:h, 0:w]
    a = np.radians(angle_deg)
    dist = np.abs(-(xx - w / 2) * np.sin(a) + (yy - h / 2) * np.cos(a))
    along = np.abs((xx - w / 2) * np.cos(a) + (yy - h / 2) * np.sin(a))
    return (dist <= thickness / 2) & (along <= 250)


def fake_sam(seam_conf=0.8, parts=None, mask=None):
    parts = parts or {}

    def sam(im, prompt):
        if prompt == "seam":
            return ([dict(object_id=0, confidence=seam_conf, mask=mask if mask is not None else line_mask())], "")
        return ([dict(object_id=0, confidence=parts[prompt])] if prompt in parts else [], "")
    return sam


class FakeEnsemble:
    def __init__(self, probs):
        self.probs = np.array(probs)

    def __call__(self, images):
        return np.tile(self.probs, (len(images), 1))


class FakeSpark:
    """Answers the first turn with a classify_stitch call and the second with a fixed verdict."""

    def __init__(self, verdict=None, fail=False):
        self.verdict, self.fail = verdict, fail
        self.chat = self
        self.completions = self

    def create(self, messages, **kw):
        if self.fail:
            raise ConnectionError("down")
        tool_turn = messages[-1]["role"] != "tool"
        call = type("Call", (), dict(id="c1", function=type("F", (), dict(name="classify_stitch", arguments="{}"))()))()
        msg = type("Msg", (), dict(content=None if tool_turn else json.dumps(self.verdict), tool_calls=[call] if tool_turn else None,
                                   model_dump=lambda self, **k: {"role": "assistant", "tool_calls": []}))()
        usage = type("U", (), dict(prompt_tokens=100, completion_tokens=20))()
        return type("R", (), dict(choices=[type("C", (), dict(message=msg))()], usage=usage))()


def test_localize_thresholds():
    loc = localize(Image.new("RGB", (100, 100)), fake_sam(seam_conf=0.4, parts={"collar": 0.69}))
    assert loc["seam"] is None and loc["part"] is None
    loc = localize(Image.new("RGB", (100, 100)), fake_sam(seam_conf=0.6, parts={"collar": 0.9, "pocket": 0.75}))
    assert loc["seam"]["confidence"] == 0.6 and (loc["part"], loc["part_conf"]) == ("collar", 0.9)


def test_tiles_are_square_and_follow_a_diagonal_seam():
    im = Image.new("RGB", (800, 600), "white")
    mask = line_mask(angle_deg=30, thickness=4)
    arr = np.array(im)
    arr[mask] = 0
    tiles = seam_tiles(Image.fromarray(arr), mask)
    assert len(tiles) == 3 and all(t.size[0] == t.size[1] for t in tiles)
    for t in tiles:  # after rotation the dark line runs across the middle row, not down a column
        g = np.array(t.convert("L")).astype(float)
        assert g.mean(axis=1).min() < g.mean(axis=0).min()


def test_retake_when_no_seam(tmp_path):
    r = inspect_photo(photo_bytes(), sam=fake_sam(seam_conf=0.2), ensemble=FakeEnsemble([0.9, 0.025, 0.025, 0.025, 0.025]),
                      client=FakeSpark(), reference=None, line_plan=PLAN, out_dir=tmp_path)
    assert r["retake"] and not r["seam_found"]


def test_agreement_maps_part_to_line_plan(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    probs = [0.05, 0.85, 0.05, 0.03, 0.02]  # good, skipped, broken, crooked, stain
    r = inspect_photo(photo_bytes(), sam=fake_sam(parts={"pocket": 0.9}), ensemble=FakeEnsemble(probs),
                      client=FakeSpark({"verdict": "confirm", "label": "skipped_stitch", "reason": "gap"}),
                      reference=ref, line_plan=PLAN, out_dir=tmp_path, part_hint="collar")
    assert r["proposed_label"] == "skipped_stitch" and not r["needs_checker"] and r["proposed_severity"] == "major"
    assert (r["part"], r["operator_id"], r["machine_id"]) == ("pocket", "OP06", "M06")


def test_spark_failure_sends_the_classifier_label_to_the_checker(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    r = inspect_photo(photo_bytes(), sam=fake_sam(), ensemble=FakeEnsemble([0.1, 0.05, 0.8, 0.03, 0.02]),
                      client=FakeSpark(fail=True), reference=ref, line_plan=PLAN, out_dir=tmp_path, part_hint="collar")
    assert r["needs_checker"] and r["proposed_label"] == "broken_stitch" and r["spark_verdict"]["spark_error"]
    assert (r["operator_id"], r["machine_id"]) == ("OP01", "M01")
