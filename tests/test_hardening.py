import base64
import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stitchcheck.backend import db, pipeline
from stitchcheck.backend.app import Deps, create_app
from test_api import PLAN, SKIPPED, shoot, start
from test_pipeline import FakeEnsemble, FakeSpark, fake_sam, photo_bytes

OLD_SCHEMA = """
CREATE TABLE session (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    style TEXT NOT NULL,
    fabric TEXT NOT NULL,
    lot_size INTEGER NOT NULL CHECK (lot_size >= 2),
    critical_parts TEXT NOT NULL DEFAULT '["collar", "placket"]',
    spot_check_rate REAL NOT NULL DEFAULT 0.1,
    is_demo INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE inspection (
    id INTEGER PRIMARY KEY,
    garment_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    photo_path TEXT NOT NULL,
    crop_path TEXT,
    sam_part TEXT,
    sam_part_conf REAL,
    seam_found INTEGER NOT NULL,
    classifier_top2 TEXT,
    spark_verdict TEXT,
    proposed_label TEXT,
    proposed_severity TEXT,
    needs_checker INTEGER NOT NULL DEFAULT 0,
    final_label TEXT,
    final_part TEXT,
    final_severity TEXT,
    operator_id TEXT,
    machine_id TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    decided_via TEXT,
    config_hash TEXT NOT NULL,
    agent_run_id INTEGER,
    timings TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO session (date, style, fabric, lot_size) VALUES ('2026-10-20', 'shirt', 'A', 300);
INSERT INTO inspection (garment_id, reason, photo_path, seam_found, proposed_label, config_hash)
    VALUES (1, 'critical', 'old.jpg', 1, 'broken_stitch', 'abc');
"""


def test_connect_upgrades_an_older_database_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.close()

    con = db.connect(path)
    ref = sqlite3.connect(":memory:")
    ref.executescript(db.SCHEMA)
    for table in ("session", "inspection", "idempotency"):
        want = {r[1] for r in ref.execute(f"PRAGMA table_info({table})")}
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        assert want <= have, (table, want - have)

    s = con.execute("SELECT * FROM session").fetchone()
    assert (s["style"], s["lot_size"], s["critical_parts"], s["brain"], s["owner"]) == ("shirt", 300, ["collar", "placket"], "spark", None)
    i = con.execute("SELECT * FROM inspection").fetchone()
    assert (i["photo_path"], i["proposed_label"], i["review_status"], i["overlay_path"], i["part_chip"]) == \
        ("old.jpg", "broken_stitch", "done", None, None)

    new = db.insert(con, "session", date="2026-10-30", style="shirt", fabric="B", lot_size=50)
    row = con.execute("SELECT created_at, brain FROM session WHERE id = ?", (new,)).fetchone()
    assert row["created_at"] and row["brain"] == "spark"  # the non-constant default still fills new rows
    assert db.connect(path).execute("SELECT COUNT(*) AS n FROM session").fetchone()["n"] == 2  # a second connect is a no-op


# The API ------------------------------------------------------------------------------------------------------------

CONFIRM = {"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}
INLINE = "\n  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');\n"


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")


def make(tmp_path, extensions=(), **kw):
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    deps = dict(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "photos"), sam=fake_sam(),
                ensemble=FakeEnsemble(SKIPPED), spark=FakeSpark(CONFIRM), transcribe=lambda p: "collar",
                reference=lambda f: ref, app_dir=str(tmp_path / "no_app"))
    return TestClient(create_app(Deps(**deps | kw), extensions=extensions))


def frontend(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    (d / "index.html").write_text(f"<!doctype html><title>StitchCheck</title><script src=\"app.js\"></script>"
                                  f"<script>{INLINE}</script>")
    (d / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (d / "app.js").write_text("console.log('hi');")
    return d


def test_frontend_is_served_at_root_after_the_api_and_extensions(tmp_path):
    def ext(app, ctx):
        @app.get("/ext/ping")
        def ping():
            return {"ok": True}

    c = make(tmp_path, extensions=[ext], app_dir=str(frontend(tmp_path)))
    r = c.get("/")
    assert r.status_code == 200 and "<title>StitchCheck</title>" in r.text and r.headers["cache-control"] == "no-cache"
    assert c.get("/index.html").headers["cache-control"] == "no-cache"
    assert c.get("/sw.js").headers["cache-control"] == "no-cache"
    assert c.get("/app.js").status_code == 200 and "no-cache" not in c.get("/app.js").headers.get("cache-control", "")
    assert c.get("/health").json() == {"ok": True} and c.get("/ext/ping").json() == {"ok": True}
    s, (g,) = start(c)
    assert c.get(f"/sessions/{s['id']}").json()["line_plan"][0]["part"] == "collar"
    assert "inspection_id" in shoot(c, g)


def test_a_missing_frontend_folder_is_skipped(tmp_path):
    c = make(tmp_path, app_dir=str(tmp_path / "nowhere"))
    assert c.get("/health").json() == {"ok": True} and c.get("/").status_code == 404


def test_cors_allows_only_the_configured_origins(tmp_path):
    c = make(tmp_path, cors_origins=["http://localhost:5500"])
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,idempotency-key"}
    ok = c.options("/sessions", headers={"Origin": "http://localhost:5500", **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5500"
    assert ok.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in c.options("/sessions", headers={"Origin": "https://evil.example", **pre}).headers
    assert c.get("/health", headers={"Origin": "http://localhost:5500"}).headers["access-control-allow-origin"] == "http://localhost:5500"
    assert Deps(db_path="", storage="", sam=None, ensemble=None, spark=None, transcribe=None,
                reference=None).cors_origins[0] == "http://localhost:8000"


def session_body(**kw):
    return dict(date="2026-10-30", style="shirt", fabric="A", lot_size=100, line_plan=PLAN) | kw


@pytest.mark.parametrize("body, words", [
    (session_body(lot_size=1), "lot size"),
    (session_body(line_plan=PLAN + [PLAN[0]]), "twice"),
    (session_body(critical_parts=["collar", "hem"]), "critical parts missing"),
    (session_body(line_plan=[PLAN[0] | {"operator_id": " "}, *PLAN[1:]]), "operator and a machine"),
    (session_body(line_plan=[PLAN[0] | {"machine_id": ""}, *PLAN[1:]]), "operator and a machine"),
    (session_body(line_plan=[]), "empty"),
])
def test_bad_sessions_get_a_clear_422(tmp_path, body, words):
    r = make(tmp_path).post("/sessions", json=body)
    assert r.status_code == 422 and words in r.json()["detail"]


def test_bundle_garment_photo_and_decision_validation(tmp_path):
    c = make(tmp_path)
    s, (g,) = start(c)
    assert c.post(f"/sessions/{s['id']}/bundles", json={"bundle_no": -3}).status_code == 422
    dup = c.post(f"/sessions/{s['id']}/bundles", json={"bundle_no": 34})
    assert dup.status_code == 409 and "already exists" in dup.json()["detail"]
    bid = c.app.state.con.execute("SELECT id FROM bundle").fetchone()["id"]
    assert c.post(f"/bundles/{bid}/garments", json={"garment_no": 1}).status_code == 409
    r = c.post(f"/garments/{g['id']}/photos", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
               data={"part": "collar", "reason": "bored"})
    assert r.status_code == 422 and "reason" in r.json()["detail"]
    iid = shoot(c, g)["inspection_id"]
    for bad in ({"label": "hole"}, {"severity": "huge"}, {"part": "zip"}):
        assert c.post(f"/inspections/{iid}/decision", json={"action": "change", **bad}).status_code == 422, bad
    ok = c.post(f"/inspections/{iid}/decision", json={"action": "change", "label": "puckering", "severity": "minor"})
    assert ok.status_code == 200 and ok.json()["final_severity"] == "minor"


def test_integrity_errors_are_409_and_crashes_are_a_plain_500(tmp_path):
    def ext(app, ctx):
        @app.get("/ext/dup")
        def dup():
            ctx.write(lambda con: con.execute("INSERT INTO garment (bundle_id, garment_no) VALUES (999, 1)"))

        @app.get("/ext/crash")
        def crash():
            raise RuntimeError("secret detail /srv/data/key")

    c = make(tmp_path, extensions=[ext])
    r = c.get("/ext/dup")
    assert r.status_code == 409 and r.json()["detail"].startswith("conflict: ")
    r = c.get("/ext/crash")
    assert r.status_code == 500 and r.json() == {"detail": "internal error"} and "secret" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"


def test_upload_limits_and_undecodable_files(tmp_path, monkeypatch):
    assert Image.MAX_IMAGE_PIXELS == 60_000_000
    c = make(tmp_path)
    s, (g,) = start(c)

    def post(data, name="p.jpg", ctype="image/jpeg"):
        return c.post(f"/garments/{g['id']}/photos", files={"image": (name, data, ctype)}, data={"part": "collar"})

    assert post(b"\xff" * (15 * 1024 * 1024 + 1)).status_code == 413
    assert post(b"\xff" * (17 * 1024 * 1024)).status_code == 413  # refused before the form is read
    r = post(b"this is not a photo", "p.txt", "text/plain")
    assert r.status_code == 422 and r.json()["detail"] == "not an image"
    assert post(photo_bytes()[:200]).status_code == 422  # truncated JPEG
    with monkeypatch.context() as m:
        m.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
        r = post(photo_bytes())
        assert r.status_code == 422 and "pixels" in r.json()["detail"]
    assert c.app.state.con.execute("SELECT COUNT(*) AS n FROM inspection").fetchone()["n"] == 0

    iid = shoot(c, g)["inspection_id"]

    def voice(data, name, ctype):
        return c.post(f"/inspections/{iid}/voice", files={"audio": (name, data, ctype)})

    assert voice(b"\0" * (10 * 1024 * 1024 + 1), "v.webm", "audio/webm").status_code == 413
    assert voice(b"<html>", "v.html", "text/html").status_code == 422
    assert voice(b"fake", "blob", "audio/webm;codecs=opus").status_code == 200
    assert voice(b"fake", "v.m4a", "application/octet-stream").status_code == 200
    assert sorted(p.suffix for p in (tmp_path / "photos").rglob("voice/*")) == [".m4a", ".webm"]


def photo_post(c, g, key=None):
    return c.post(f"/garments/{g['id']}/photos", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
                  data={"part": "collar", "reason": "critical"}, headers={"Idempotency-Key": key} if key else {})


def test_idempotency_key_replays_a_photo_without_a_second_inspection(tmp_path):
    c = make(tmp_path)
    s, (g,) = start(c)
    a, b = photo_post(c, g, "k1"), photo_post(c, g, "k1")
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    count = lambda: c.app.state.con.execute("SELECT COUNT(*) AS n FROM inspection").fetchone()["n"]
    assert count() == 1
    photo_post(c, g)
    photo_post(c, g)
    assert count() == 3  # no header: every post is new


def test_idempotency_key_on_complete_creates_alerts_once(tmp_path):
    c = make(tmp_path)
    s, gs = start(c, n_garments=3)
    for g in gs:
        iid = shoot(c, g)["inspection_id"]
        c.post(f"/inspections/{iid}/decision", json={"action": "agree"}, headers={"Idempotency-Key": f"d{iid}"})
    for g in gs[:2]:
        c.post(f"/garments/{g['id']}/complete")
    first = c.post(f"/garments/{gs[2]['id']}/complete", headers={"Idempotency-Key": "done-3"}).json()
    again = c.post(f"/garments/{gs[2]['id']}/complete", headers={"Idempotency-Key": "done-3"}).json()
    assert len(first["new_alerts"]) == 1 and again == first
    con = c.app.state.con
    assert con.execute("SELECT COUNT(*) AS n FROM alert").fetchone()["n"] == 1
    assert con.execute("SELECT COUNT(*) AS n FROM agent_run WHERE kind = 'alert'").fetchone()["n"] <= 1
    assert c.post(f"/garments/{gs[2]['id']}/complete").json()["new_alerts"] == []  # no header: the work runs again


def test_a_failed_request_frees_its_key_and_a_running_one_holds_it(tmp_path):
    c = make(tmp_path)
    s, (g,) = start(c)
    iid = shoot(c, g)["inspection_id"]
    h = {"Idempotency-Key": "x"}
    assert c.post(f"/inspections/{iid}/decision", json={"action": "change", "label": "hole"}, headers=h).status_code == 422
    assert c.post(f"/inspections/{iid}/decision", json={"action": "not_a_defect"}, headers=h).json()["decision"] == "not_a_defect"
    con = c.app.state.con
    con.execute("INSERT INTO idempotency (key, path, status) VALUES ('busy', ?, 0)", (f"/garments/{g['id']}/photos",))
    con.commit()
    assert photo_post(c, g, "busy").status_code == 409
    assert con.execute("SELECT status FROM idempotency WHERE key = 'busy'").fetchone()["status"] == 0


def test_a_review_that_raises_is_finished_for_the_checker(tmp_path, caplog):
    def broken_reference(fabric):
        raise OSError("reference crop missing")

    c = make(tmp_path, reference=broken_reference)
    s, (g,) = start(c)
    with caplog.at_level(logging.INFO, logger="stitchcheck.api"):
        iid = shoot(c, g)["inspection_id"]
    i = c.get(f"/inspections/{iid}").json()
    assert (i["review_status"], i["needs_checker"], i["proposed_label"], i["proposed_severity"]) == ("done", 1, "skipped_stitch", "major")
    assert "reference crop missing" in i["spark_verdict"]["spark_error"] and i["spark_verdict"]["brain"] is None
    assert (i["final_part"], i["operator_id"], i["machine_id"]) == ("collar", "OP01", "M01")
    review = [json.loads(r.message) for r in caplog.records if r.message.startswith('{"event": "review"')]
    assert review and "reference crop missing" in review[0]["error"] and review[0]["brain"] is None


def test_a_review_stuck_past_the_timeout_is_finished_on_read(tmp_path):
    c = make(tmp_path, review_timeout=5)
    s, (g,) = start(c)
    con = c.app.state.con
    rows = {}
    for name, age in (("stuck", 10), ("fresh", 0)):
        cur = con.execute("INSERT INTO inspection (garment_id, reason, photo_path, seam_found, classifier_top2, config_hash, "
                          "created_at) VALUES (?, 'critical', 'p.jpg', 1, ?, 'x', datetime('now', ?))",
                          (g["id"], json.dumps([["broken_stitch", 0.7], ["good", 0.2]]), f"-{age} seconds"))
        rows[name] = cur.lastrowid
    con.commit()
    i = c.get(f"/inspections/{rows['stuck']}").json()
    assert (i["review_status"], i["needs_checker"], i["proposed_label"], i["proposed_severity"]) == ("done", 1, "broken_stitch", "major")
    assert i["spark_verdict"]["spark_error"] == "review timed out" and i["spark_verdict"]["brain"] is None
    assert c.get(f"/inspections/{rows['fresh']}").json()["review_status"] == "reviewing"
    assert c.post(f"/inspections/{rows['fresh']}/decision", json={"action": "agree"}).status_code == 409
    assert c.post(f"/inspections/{rows['stuck']}/decision", json={"action": "agree"}).json()["final_label"] == "broken_stitch"


def test_version(tmp_path, monkeypatch):
    c = make(tmp_path)
    monkeypatch.setenv("STITCHCHECK_COMMIT", "abc1234")
    lock = Path("results/test/lock.json")
    want = hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.exists() else None
    assert c.get("/version").json() == {"commit": "abc1234", "config_hash": pipeline.config_hash(), "lock_sha": want}
    monkeypatch.delenv("STITCHCHECK_COMMIT")
    monkeypatch.chdir(tmp_path)
    v = c.get("/version").json()
    assert (v["commit"], v["lock_sha"], v["config_hash"]) == ("unknown", None, pipeline.config_hash())


def test_security_headers_with_the_inline_script_hash(tmp_path):
    c = make(tmp_path, app_dir=str(frontend(tmp_path)), cors_origins=["http://localhost:5500"])
    digest = base64.b64encode(hashlib.sha256(INLINE.encode()).digest()).decode()
    for path in ("/", "/health", "/nothing-here"):
        h = c.get(path).headers
        csp = dict(d.strip().split(" ", 1) for d in h["content-security-policy"].split(";"))
        assert csp["script-src"] == f"'self' 'sha256-{digest}'" and "unsafe-inline" not in csp["script-src"]
        assert csp["connect-src"] == "'self' http://localhost:5500" and csp["frame-ancestors"] == "'none'"
        assert csp["default-src"] == "'self'" and csp["img-src"] == "'self' blob: data:"
        assert csp["media-src"] == "'self' blob:" and csp["style-src"] == "'self' 'unsafe-inline'"
        assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer"
        assert h["permissions-policy"] == "camera=(self), microphone=(self), geolocation=()"


def test_one_json_log_line_per_request_with_the_route_template(tmp_path, caplog):
    c = make(tmp_path)
    s, (g,) = start(c)
    with caplog.at_level(logging.INFO, logger="stitchcheck.api"):
        caplog.clear()
        iid = shoot(c, g)["inspection_id"]
        c.get(f"/inspections/{iid}")
        c.get("/nothing-here")
    lines = [json.loads(r.message) for r in caplog.records if r.name == "stitchcheck.api"]
    req = [x for x in lines if "event" not in x]
    assert [(x["path"], x["method"], x["status"]) for x in req] == [
        ("/garments/{gid}/photos", "POST", 200), ("/inspections/{iid}", "GET", 200), ("/nothing-here", "GET", 404)]
    assert all(set(x) == {"path", "method", "status", "ms"} for x in req)
    review = [x for x in lines if x.get("event") == "review"]
    assert len(review) == 1 and set(review[0]) == {"event", "inspection_id", "brain", "fell_back", "cost_usd", "ms", "error"}
    assert review[0]["brain"] == "spark" and review[0]["error"] is None and review[0]["inspection_id"] == iid
