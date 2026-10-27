import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stitchcheck.backend.app import Deps, create_app
from stitchcheck.backend.brains import OPEN, SPARK, Brains
from test_api import SKIPPED, shoot, start
from test_pipeline import FakeEnsemble, FakeSpark, fake_sam

VERDICT = {"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}


def test_order_fallback_and_caps():
    b = Brains({SPARK: "s", OPEN: "o"})
    assert [x.name for x in b.order(SPARK)] == [SPARK, OPEN] and [x.name for x in b.order(OPEN)] == [OPEN]
    assert b.call(SPARK, lambda br: br.name) == (SPARK, b.available[SPARK], False)
    fail_spark = lambda br: (_ for _ in ()).throw(ConnectionError("down")) if br.name == SPARK else "open answered"
    assert b.call(SPARK, fail_spark)[1:] == (b.available[OPEN], True)
    capped = Brains({SPARK: "s", OPEN: "o"}, spent=lambda n: 99.0 if n == SPARK else 0.0)
    assert [x.name for x in capped.order(SPARK)] == [OPEN]
    with pytest.raises(RuntimeError):
        Brains({SPARK: "s"}, spent=lambda n: 99.0).call(SPARK, lambda br: 1)
    assert [x.name for x in Brains({SPARK: "s"}).order(OPEN)] == [SPARK]  # Open not configured: Spark


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)

    def make(spark, spark_open):
        deps = Deps(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "p"), sam=fake_sam(), ensemble=FakeEnsemble(SKIPPED),
                    spark=spark, spark_open=spark_open, transcribe=lambda p: "", reference=lambda f: ref)
        return TestClient(create_app(deps))
    return make


def test_meta_api_failure_falls_back_to_open_and_is_recorded(make):
    c = make(FakeSpark(fail=True), FakeSpark(VERDICT))
    s, (g,) = start(c)
    insp = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert insp["proposed_label"] == "skipped_stitch" and insp["spark_verdict"]["brain"] == OPEN and insp["spark_verdict"]["fell_back"]
    run = c.get(f"/agent-runs/{insp['agent_run_id']}").json()
    assert run["brain"] == OPEN and run["fell_back"] == 1 and run["cost_usd"] > 0


def test_session_mode_open_and_brains_listing(make):
    c = make(FakeSpark(VERDICT), FakeSpark(VERDICT))
    s, (g,) = start(c)
    assert c.post(f"/sessions/{s['id']}/brain", json={"brain": "open"}).json()["brain"] == OPEN
    assert c.post(f"/sessions/{s['id']}/brain", json={"brain": "gpt"}).status_code == 422
    insp = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert insp["spark_verdict"]["brain"] == OPEN and not insp["spark_verdict"]["fell_back"]
    modes = {m["brain"]: m for m in c.get("/brains").json()}
    assert modes[SPARK]["label"].startswith("Low false alarm") and modes[OPEN]["major_recall"] == 0.944
    assert modes[OPEN]["spent_today_usd"] > 0


def test_no_brain_sends_the_classifier_label_to_the_checker(make):
    c = make(FakeSpark(fail=True), None)
    s, (g,) = start(c)
    insp = c.get(f"/inspections/{shoot(c, g)['inspection_id']}").json()
    assert insp["needs_checker"] == 1 and insp["proposed_label"] == "skipped_stitch" and insp["spark_verdict"]["brain"] is None


def test_per_ip_limit(make, monkeypatch):
    monkeypatch.setattr("stitchcheck.backend.app.PER_IP_PER_HOUR", 2)
    c = make(FakeSpark(VERDICT), None)
    s, (g,) = start(c)
    assert [shoot(c, g).get("detail") for _ in range(2)] == [None, None]
    r = c.post(f"/garments/{g['id']}/photos", files={"image": ("p.jpg", b"x", "image/jpeg")}, data={"part": "collar"})
    assert r.status_code == 429
