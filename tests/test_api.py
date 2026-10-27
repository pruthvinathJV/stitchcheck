import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stitchcheck.backend.app import Deps, create_app
from test_pipeline import FakeEnsemble, FakeSpark, fake_sam, photo_bytes

PLAN = [dict(part="collar", operator_id="OP01", machine_id="M01"), dict(part="placket", operator_id="OP02", machine_id="M02"),
        dict(part="cuff", operator_id="OP03", machine_id="M03")]
SKIPPED = [0.05, 0.85, 0.05, 0.03, 0.02]  # good, skipped, broken, crooked, stain


class VoiceSpark(FakeSpark):
    """Inspect calls get the tool flow; a voice-mapping prompt gets a parsed correction."""

    def create(self, messages, **kw):
        text = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        if "spoke a correction" in text:
            msg = type("M", (), dict(content=json.dumps({"label": "broken_stitch", "machine_id": "M99", "part": "collar",
                                                         "ambiguous": []}), tool_calls=None))()
            usage = type("U", (), dict(prompt_tokens=50, completion_tokens=10))()
            return type("R", (), dict(choices=[type("C", (), dict(message=msg))()], usage=usage))()
        return super().create(messages, **kw)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    deps = Deps(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "photos"), sam=fake_sam(parts={"pocket": 0.9}),
                ensemble=FakeEnsemble(SKIPPED),
                spark=VoiceSpark({"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}),
                transcribe=lambda path: "machine one collar దగ్గర తెగింది", reference=lambda fabric: ref)
    return TestClient(create_app(deps))


def start(c, n_garments=1):
    s = c.post("/sessions", json=dict(date="2026-10-28", style="men's shirt", fabric="A", lot_size=500, line_plan=PLAN)).json()
    b = c.post(f"/sessions/{s['id']}/bundles", json={"bundle_no": 34}).json()
    gs = [c.post(f"/bundles/{b['id']}/garments", json={"garment_no": n}).json() for n in range(1, n_garments + 1)]
    return s, gs


def shoot(c, g, part="collar"):
    return c.post(f"/garments/{g['id']}/photos", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
                  data={"part": part, "reason": "critical"}).json()


def test_photo_review_decision_dhu_and_trace(client):
    s, (g,) = start(client)
    assert g["photograph"][:2] == ["collar", "placket"]
    r = shoot(client, g)
    assert r["review_status"] == "reviewing" and r["classifier_label"] == "skipped_stitch" and r["scale_path"]
    insp = client.get(f"/inspections/{r['inspection_id']}").json()  # the background review has run in TestClient
    assert insp["review_status"] == "done" and insp["proposed_label"] == "skipped_stitch" and not insp["needs_checker"]
    assert (insp["operator_id"], insp["machine_id"], insp["final_part"]) == ("OP01", "M01", "collar")
    assert insp["part_chip"] == "pocket"  # SAM disagrees with the sequence: chip only, the part stays collar
    assert "step1" in insp["timings"] and "step2" in insp["timings"]

    trace = client.get(f"/agent-runs/{insp['agent_run_id']}").json()
    assert trace["steps"][0]["call"]["name"] == "classify_stitch" and trace["steps"][0]["output"]["top2"][0][0] == "skipped_stitch"

    d = client.post(f"/inspections/{r['inspection_id']}/decision", json={"action": "agree"}).json()
    assert d["decision"] == "agree" and d["final_label"] == "skipped_stitch" and d["final_severity"] == "major"
    assert client.get(f"/sessions/{s['id']}/dashboard").json()["dhu"] == 0  # garment not completed yet
    client.post(f"/garments/{g['id']}/complete")
    dash = client.get(f"/sessions/{s['id']}/dashboard").json()
    assert dash["dhu"] == 100.0 and dash["defective_pct"] == 100.0 and dash["by"]["machine"]["M01"]["defects"] == 1


def test_change_and_not_a_defect(client):
    s, (g,) = start(client)
    iid = shoot(client, g)["inspection_id"]
    d = client.post(f"/inspections/{iid}/decision", json={"action": "change", "label": "broken_stitch", "part": "placket"}).json()
    assert (d["decision"], d["final_label"], d["operator_id"], d["machine_id"]) == ("changed", "broken_stitch", "OP02", "M02")
    d = client.post(f"/inspections/{iid}/decision", json={"action": "not_a_defect"}).json()
    assert d["final_label"] == "good" and d["final_severity"] == "none"
    assert client.post(f"/inspections/{iid}/decision", json={"action": "reject"}).status_code == 422


def test_voice_proposes_but_saves_nothing_and_flags_unknown_ids(client):
    s, (g,) = start(client)
    iid = shoot(client, g)["inspection_id"]
    v = client.post(f"/inspections/{iid}/voice", files={"audio": ("v.m4a", b"fake", "audio/mp4")}).json()
    assert v["proposed"] == {"label": "broken_stitch", "part": "collar"} and v["ambiguous"] == ["machine_id"]
    assert client.get(f"/inspections/{iid}").json()["decision"] == "pending"


def test_retake_when_no_seam(tmp_path, monkeypatch):
    deps = Deps(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "p"), sam=fake_sam(seam_conf=0.1),
                ensemble=FakeEnsemble(SKIPPED), spark=FakeSpark(), transcribe=lambda p: "", reference=lambda f: None)
    c = TestClient(create_app(deps))
    s, (g,) = start(c)
    r = shoot(c, g)
    assert r["retake"] == "Move closer to the stitching" and "inspection_id" not in r


def test_trend_alert_after_three_skipped_on_one_machine(client):
    s, gs = start(client, n_garments=4)
    for g in gs[:3]:
        iid = shoot(client, g)["inspection_id"]
        client.post(f"/inspections/{iid}/decision", json={"action": "agree"})
        out = client.post(f"/garments/{g['id']}/complete").json()
    assert out["new_alerts"] == [{"id": 1, "machine_id": "M01", "defect": "skipped_stitch", "garment_ids": [g["id"] for g in gs[:3]]}]
    client.post(f"/garments/{gs[3]['id']}/complete")
    assert len(client.get(f"/sessions/{s['id']}/dashboard").json()["alerts"]) == 1  # no repeat alert


def test_report_endpoint_stores_report_and_trace(tmp_path, monkeypatch):
    from test_report import ScriptedSpark
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    spark = ScriptedSpark(["## Buyer summary\nDHU {{dhu.session.dhu}} on {{dhu.session.garments}} garments."])
    deps = Deps(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "p"), sam=fake_sam(), ensemble=FakeEnsemble(SKIPPED),
                spark=spark, transcribe=lambda p: "", reference=lambda f: None)
    c = TestClient(create_app(deps))
    s, (g,) = start(c)
    c.post(f"/garments/{g['id']}/complete")
    r = c.post(f"/sessions/{s['id']}/report").json()
    assert r["validator_ok"] == 1 and r["body_md"].endswith("DHU 0.0 on 1 garments.")
    trace = c.get(f"/agent-runs/{r['agent_run_id']}").json()
    assert trace["kind"] == "report" and trace["steps"][0]["call"]["name"] == "get_dhu"
