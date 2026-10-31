import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stitchcheck.backend import access, demo_api
from stitchcheck.backend.app import Deps, create_app
from stitchcheck.backend.db import completed_garments, connect, session_inspections
from stitchcheck.backend.demo import seed_demo
from stitchcheck.qc import counts
from test_api import SKIPPED, VoiceSpark
from test_pipeline import FakeEnsemble, fake_sam, photo_bytes

ENV = ("STITCHCHECK_UNIT_PIN", "STITCHCHECK_SAFE_MODE", "STITCHCHECK_ADMIN_TOKEN", "STITCHCHECK_SECRET",
       "STITCHCHECK_SAM_CACHE")
ROOT = Path(__file__).resolve().parents[1]


class CountingSam:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __call__(self, im, prompt):
        self.calls.append(prompt)
        return self.inner(im, prompt)


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    (gallery / "g1.jpg").write_bytes(photo_bytes())
    (gallery / "manifest.json").write_text(json.dumps([
        {"id": "g1", "title_te": "కాలర్", "title_en": "Collar", "part": "collar", "image": "g1.jpg", "credit": "StitchCheck"}]))
    monkeypatch.setenv("STITCHCHECK_GALLERY_DIR", str(gallery))
    return tmp_path


def make_app(tmp, sam=None, spark=None):
    ref = tmp / "ref.jpg"
    if not ref.exists():
        Image.new("RGB", (224, 224)).save(ref)
    sam = sam or CountingSam(fake_sam(parts={"pocket": 0.9}))
    deps = Deps(db_path=str(tmp / "t.db"), storage=str(tmp / "photos"), sam=sam, ensemble=FakeEnsemble(SKIPPED),
                spark=spark or VoiceSpark({"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}),
                transcribe=lambda p: "machine one", reference=lambda f: ref)
    app = create_app(deps, extensions=[access.install, demo_api.register])
    seed = seed_demo(app.state.con)
    return app, seed, sam


# ---- 1. the seeded demo day --------------------------------------------------------------------------------------

def test_seeded_day_looks_like_a_decent_new_unit(tmp_path):
    con = connect(tmp_path / "d.db")
    sid = seed_demo(con)
    assert seed_demo(con) == sid  # idempotent
    s = con.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()
    assert s["is_demo"] == 1 and s["owner"] is None
    insp, done = session_inspections(con, sid), completed_garments(con, sid)
    assert len(done) == 60
    assert 5 <= counts.dhu(insp, done) <= 9
    assert 0.10 <= sum(i["needs_checker"] for i in insp) / len(insp) <= 0.20
    alerts = con.execute("SELECT * FROM alert WHERE session_id = ?", (sid,)).fetchall()
    assert any(a["machine_id"] == "7" and a["defect"] == "skipped_stitch" for a in alerts)
    flagged = {i["machine_id"] for i in insp if i["proposed_label"] != "good"}
    assert len(flagged) >= 3  # the defect mix (flags, overruled or waiting for the checker) spans machines
    confirmed = counts.confirmed_defects(insp, done)
    assert len({i["machine_id"] for i in confirmed}) >= 3  # confirmed defects on machine 7 and at least two others


def test_an_older_seed_is_replaced(tmp_path):
    con = connect(tmp_path / "d.db")
    old = con.execute("INSERT INTO session (date, style, fabric, lot_size, is_demo) VALUES ('d', 's', 'A', 500, 1)").lastrowid
    b = con.execute("INSERT INTO bundle (session_id, bundle_no) VALUES (?, 1)", (old,)).lastrowid
    g = con.execute("INSERT INTO garment (bundle_id, garment_no) VALUES (?, 1)", (b,)).lastrowid
    con.execute("INSERT INTO inspection (garment_id, reason, photo_path, seam_found, config_hash) "
                "VALUES (?, 'critical', 'demo', 1, 'demo')", (g,))
    con.commit()
    sid = seed_demo(con)  # the id may be reused; the old rows are gone
    assert con.execute("SELECT COUNT(*) AS n FROM inspection WHERE config_hash = 'demo'").fetchone()["n"] == 0
    assert len(completed_garments(con, sid)) == 60 and seed_demo(con) == sid


# ---- 2. per-visitor copies ---------------------------------------------------------------------------------------

def test_demo_session_is_a_private_deep_copy_with_an_empty_bundle(env):
    app, seed, _ = make_app(env)
    c = TestClient(app)
    r = c.post("/demo/session")
    cookie = r.headers["set-cookie"]
    assert "sc_visitor=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Max-Age=86400" in cookie and "Secure" not in cookie
    out = r.json()
    sid, bid = out["session_id"], out["bundle_id"]
    assert sid != seed
    row = app.state.con.execute("SELECT * FROM session WHERE id = ?", (sid,)).fetchone()
    assert row["is_demo"] == 1 and row["owner"]
    dash, seed_dash = c.get(f"/sessions/{sid}/dashboard").json(), c.get(f"/sessions/{seed}/dashboard").json()
    assert dash["completed_garments"] == 60 and dash["dhu"] == seed_dash["dhu"] and dash["inspections"] == seed_dash["inspections"]
    copy_garments = {r["id"] for r in app.state.con.execute(
        "SELECT g.id FROM garment g JOIN bundle b ON b.id = g.bundle_id WHERE b.session_id = ?", (sid,)).fetchall()}
    assert dash["alerts"] and all(set(a["garment_ids"]) <= copy_garments for a in dash["alerts"])
    assert app.state.con.execute("SELECT session_id, bundle_no FROM bundle WHERE id = ?", (bid,)).fetchone() == \
        {"session_id": sid, "bundle_no": 7}
    g = c.post(f"/bundles/{bid}/garments", json={"garment_no": 1})
    assert g.status_code == 200
    assert c.post("/demo/session").json() == out  # the live copy comes back
    other = TestClient(app)
    out2 = other.post("/demo/session").json()
    assert out2["session_id"] not in (sid, seed)
    assert other.get(f"/sessions/{sid}").status_code == 404


def test_reset_replaces_the_copy_and_its_photos(env):
    app, seed, _ = make_app(env)
    c = TestClient(app)
    first = c.post("/demo/session").json()
    folder = env / "photos" / str(first["session_id"])
    folder.mkdir(parents=True)
    (folder / "x.jpg").write_bytes(b"x")
    second = c.post("/demo/reset").json()
    assert second["session_id"] != first["session_id"] and not folder.exists()
    assert app.state.con.execute("SELECT id FROM session WHERE id = ?", (first["session_id"],)).fetchone() is None
    assert c.get(f"/sessions/{second['session_id']}").status_code == 200


def test_copies_older_than_a_day_are_cleaned_up(env, monkeypatch):
    app, seed, _ = make_app(env)
    a, b = TestClient(app), TestClient(app)
    old = a.post("/demo/session").json()["session_id"]
    app.state.con.execute("UPDATE session SET created_at = datetime('now', '-25 hours') WHERE id = ?", (old,))
    app.state.con.commit()
    b.post("/demo/session")  # cleaned at most once an hour: not yet
    assert app.state.con.execute("SELECT id FROM session WHERE id = ?", (old,)).fetchone()
    app2, _, _ = make_app(env)  # on start
    assert app2.state.con.execute("SELECT id FROM session WHERE id = ?", (old,)).fetchone() is None

    old2 = a.post("/demo/session").json()["session_id"]
    app.state.con.execute("UPDATE session SET created_at = datetime('now', '-25 hours') WHERE id = ?", (old2,))
    app.state.con.commit()
    monkeypatch.setattr(demo_api, "CLEANUP_EVERY_S", 0)
    b.get("/demo/gallery")  # lazily, on a demo request
    assert app.state.con.execute("SELECT id FROM session WHERE id = ?", (old2,)).fetchone() is None


def test_gallery_list_media_and_missing_folder(env, monkeypatch):
    app, _, _ = make_app(env)
    c = TestClient(app)
    items = c.get("/demo/gallery").json()
    assert items == [{"id": "g1", "title_te": "కాలర్", "title_en": "Collar", "part": "collar",
                      "thumb_url": "/gallery-media/g1.jpg"}]
    assert c.get(items[0]["thumb_url"]).status_code == 200
    assert c.get("/gallery-media/manifest.json").status_code == 404
    assert c.get("/gallery-media/..%2Ft.db").status_code == 404
    monkeypatch.setenv("STITCHCHECK_GALLERY_DIR", str(env / "nowhere"))
    assert c.get("/demo/gallery").json() == []


def test_gallery_shot_creates_a_real_inspection_and_caches_sam(env):
    app, seed, sam = make_app(env)
    c = TestClient(app)
    d = c.post("/demo/session").json()
    g = c.post(f"/bundles/{d['bundle_id']}/garments", json={"garment_no": 1}).json()
    r = c.post("/demo/gallery/g1", json={"garment_id": g["id"], "part": "collar"}).json()
    assert set(r) == {"inspection_id", "review_status", "crop_url", "overlay_url", "classifier_label",
                      "classifier_top2", "scale_path", "step1_seconds"}
    assert r["review_status"] == "reviewing" and r["classifier_label"] == "skipped_stitch"
    insp = c.get(f"/inspections/{r['inspection_id']}").json()
    assert insp["reason"] == "suspicious" and insp["review_status"] == "done" and insp["proposed_label"] == "skipped_stitch"
    assert insp["part_chip"] == "pocket" and c.get(insp["crop_url"]).status_code == 200
    calls = len(sam.calls)
    assert sam.calls.count("seam") == 1 and calls == 4  # seam + the three part prompts
    r2 = c.post("/demo/gallery/g1", json={"garment_id": g["id"], "part": "collar"}).json()
    assert r2["inspection_id"] != r["inspection_id"] and len(sam.calls) == calls  # all from the cache
    assert c.get(f"/inspections/{r2['inspection_id']}").json()["part_chip"] == "pocket"

    assert c.post("/demo/gallery/nope", json={"garment_id": g["id"], "part": "collar"}).status_code == 404
    assert c.post("/demo/gallery/g1", json={"garment_id": g["id"], "part": "elbow"}).status_code == 422
    seed_garment = app.state.con.execute("SELECT g.id FROM garment g JOIN bundle b ON b.id = g.bundle_id "
                                         "WHERE b.session_id = ? LIMIT 1", (seed,)).fetchone()["id"]
    assert c.post("/demo/gallery/g1", json={"garment_id": seed_garment, "part": "collar"}).status_code == 404
    other = TestClient(app)
    other.post("/demo/session")
    assert other.post("/demo/gallery/g1", json={"garment_id": g["id"], "part": "collar"}).status_code == 404


def test_gallery_retake_has_the_photo_route_shape(env):
    app, _, _ = make_app(env, sam=fake_sam(seam_conf=0.1))
    c = TestClient(app)
    d = c.post("/demo/session").json()
    g = c.post(f"/bundles/{d['bundle_id']}/garments", json={"garment_no": 1}).json()
    r = c.post("/demo/gallery/g1", json={"garment_id": g["id"], "part": "collar"}).json()
    assert set(r) == {"retake", "step1_seconds"}


# ---- 4. safe mode on the gallery ---------------------------------------------------------------------------------

def test_safe_mode_gallery_works_only_from_the_cache(env, monkeypatch):
    app, _, sam = make_app(env)
    c = TestClient(app)
    d = c.post("/demo/session").json()
    g = c.post(f"/bundles/{d['bundle_id']}/garments", json={"garment_no": 1}).json()
    shot = {"garment_id": g["id"], "part": "collar"}
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "1")
    r = c.post("/demo/gallery/g1", json=shot)
    assert r.status_code == 503 and r.json()["detail"] == "Live AI paused for the demo; try the gallery"
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "0")
    assert c.post("/demo/gallery/g1", json=shot).status_code == 200  # fills the SAM and Spark caches
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "1")
    calls = len(sam.calls)
    r = c.post("/demo/gallery/g1", json=shot)
    assert r.status_code == 200 and len(sam.calls) == calls
    insp = c.get(f"/inspections/{r.json()['inspection_id']}").json()
    assert insp["review_status"] == "done" and insp["spark_verdict"]["cached"] and not insp["spark_verdict"]["spark_error"]
    for f in (env / "cache").iterdir():  # Spark answer gone: the review would need a live call
        f.unlink()
    assert c.post("/demo/gallery/g1", json=shot).status_code == 503


# ---- 6. the gallery build script ---------------------------------------------------------------------------------

def test_build_gallery_from_a_fake_folder(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("build_gallery", ROOT / "scripts/ops/build_gallery.py")
    bg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bg)
    shirt = tmp_path / "shirt"
    shirt.mkdir()
    for n in ("collar", "full"):
        Image.new("RGB", (64, 64)).save(shirt / f"{n}.jpeg")
    seg = tmp_path / "seg"
    rows = []
    for fabric in ("Cotton", "Oxford"):
        for label in ("good", "skipped_stitch", "broken_stitch", "crooked_seam", "stain_damage"):
            for k in range(3):
                rel = f"{fabric}/{label}/{k}.jpg"
                (seg / rel).parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (32, 32)).save(seg / rel)
                rows.append(dict(path=rel, fabric=fabric, label=label, split="test", licence="CC-BY-4.0"))
    rows.append(dict(path="x.jpg", fabric="Denim", label="good", split="train", licence="CC-BY-4.0"))
    out = tmp_path / "gallery"
    items = bg.build(out, shirt, ROOT / "docs/ui_strings.json", split=pd.DataFrame(rows), image_path=lambda r: seg / r)
    assert len(items) == 10 and len({i["id"] for i in items}) == 10
    sn = [i for i in items if i["credit"] == "StitchingNet-Seg, CC BY 4.0"]
    assert len(sn) == 8 and len({i["title_en"].split(",")[0] for i in sn}) == 5
    assert {i["id"] for i in items if i["credit"] == "StitchCheck"} == {"shirt-collar", "shirt-full"}
    assert json.loads((out / "manifest.json").read_text()) == items
    assert all((out / i["image"]).exists() and i["title_te"] and i["part"] for i in items)
    assert items[0]["title_en"] == "Shirt collar"
    for i in items:
        assert "\u2013" not in i["title_en"] + i["title_te"] and "\u2014" not in i["title_en"] + i["title_te"]
    assert bg.build(out, shirt, ROOT / "docs/ui_strings.json", split=pd.DataFrame(rows), image_path=lambda r: seg / r) == items


def test_seeded_alerts_get_their_work_order_drafted(tmp_path):
    import time as _time
    from stitchcheck.backend.app import Deps, _draft_missing_work_orders, create_app
    from stitchcheck.backend.demo import seed_demo
    from test_pipeline import FakeEnsemble, fake_sam

    class WorkOrderSpark:
        def __init__(self):
            self.chat = self.completions = self

        def create(self, messages, **kw):
            msg = type("M", (), dict(content="Check machine {{alert.machine}}: needle and timing.", tool_calls=None))()
            usage = type("U", (), dict(prompt_tokens=10, completion_tokens=5))()
            return type("R", (), dict(choices=[type("C", (), dict(message=msg))()], usage=usage))()

    app = create_app(Deps(db_path=str(tmp_path / "w.db"), storage=str(tmp_path / "p"), sam=fake_sam(),
                          ensemble=FakeEnsemble([0.9, 0.025, 0.025, 0.025, 0.025]), spark=WorkOrderSpark(),
                          transcribe=lambda p: "", reference=lambda f: None))
    sid = seed_demo(app.state.con)
    _draft_missing_work_orders(app, sid)
    for _ in range(50):
        rows = app.state.con.execute("SELECT body_md FROM alert WHERE session_id = ?", (sid,)).fetchall()
        if rows and all(r["body_md"] for r in rows):
            break
        _time.sleep(0.05)
    assert rows and all(r["body_md"] == "Check machine 7: needle and timing." for r in rows)
