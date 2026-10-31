"""One test per failure the demo can meet: what the checker sees when a dependency is down."""
import pytest

from stitchcheck.backend import db
from stitchcheck.backend.brains import DAILY_CAP_USD, SPARK
from test_api import shoot, start
from test_hardening import CONFIRM, make, photo_post
from test_pipeline import FakeSpark, fake_sam

OPEN_VERDICT = {"verdict": "confirm", "label": "skipped_stitch", "reason": "gap in the stitch line"}


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")


def count(c, table):
    return c.app.state.con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_sam_down_is_a_503_and_no_inspection(tmp_path):
    def sam(im, prompt):
        raise ConnectionError("SAM endpoint unreachable")

    c = make(tmp_path, sam=sam)
    s, (g,) = start(c)
    r = photo_post(c, g)
    assert r.status_code == 503 and r.json() == {"detail": "Seam finder unavailable, try again"}
    assert count(c, "inspection") == 0
    assert photo_post(c, g, "retry-1").status_code == 503 and count(c, "idempotency") == 0  # the retry key is freed


def test_transcription_down_is_a_503_asking_to_type(tmp_path):
    def transcribe(path):
        raise RuntimeError("speech service timeout")

    c = make(tmp_path, transcribe=transcribe)
    s, (g,) = start(c)
    iid = shoot(c, g)["inspection_id"]
    r = c.post(f"/inspections/{iid}/voice", files={"audio": ("v.webm", b"fake", "audio/webm")})
    assert r.status_code == 503 and r.json() == {"detail": "Voice not available, type the correction instead"}
    assert count(c, "voice_note") == 0
    assert c.post(f"/inspections/{iid}/decision", json={"action": "change", "label": "broken_stitch"}).status_code == 200


def test_meta_api_down_muse_spark_open_answers(tmp_path):
    c = make(tmp_path, spark=FakeSpark(fail=True), spark_open=FakeSpark(OPEN_VERDICT))
    s, (g,) = start(c)
    i = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert i["review_status"] == "done" and i["proposed_label"] == "skipped_stitch" and not i["needs_checker"]
    assert i["spark_verdict"]["brain"] == "open" and i["spark_verdict"]["fell_back"] is True


def test_both_brains_down_the_classifier_label_goes_to_the_checker(tmp_path):
    c = make(tmp_path, spark=FakeSpark(fail=True), spark_open=FakeSpark(fail=True))
    s, (g,) = start(c)
    i = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert (i["review_status"], i["proposed_label"], i["proposed_severity"], i["needs_checker"]) == ("done", "skipped_stitch", "major", 1)
    assert i["spark_verdict"]["brain"] is None and i["spark_verdict"]["spark_error"]
    r = c.post(f"/sessions/{s['id']}/report")
    assert r.status_code == 503 and r.json()["detail"].startswith("no AI brain available")


def test_daily_cap_reached_muse_spark_open_answers(tmp_path):
    c = make(tmp_path, spark=FakeSpark(CONFIRM), spark_open=FakeSpark(OPEN_VERDICT))
    db.insert(c.app.state.con, "agent_run", kind="inspect", brain=SPARK, cost_usd=DAILY_CAP_USD[SPARK])
    assert next(b for b in c.get("/brains").json() if b["brain"] == SPARK)["spent_today_usd"] == DAILY_CAP_USD[SPARK]
    s, (g,) = start(c)
    i = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert i["review_status"] == "done" and i["spark_verdict"]["brain"] == "open" and not i["needs_checker"]


def test_no_seam_is_a_retake_not_an_error(tmp_path):
    c = make(tmp_path, sam=fake_sam(seam_conf=0.1))
    s, (g,) = start(c)
    r = photo_post(c, g)
    assert r.status_code == 200 and r.json()["retake"] == "Move closer to the stitching" and count(c, "inspection") == 0
