import pytest
from fastapi.testclient import TestClient

from stitchcheck.backend import db
from test_api import PLAN, start
from test_demo_api import ENV, make_app
from test_pipeline import FakeSpark, photo_bytes

PAUSED = "Live AI paused for the demo; try the gallery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("STITCHCHECK_GALLERY_DIR", str(tmp_path / "no-gallery"))
    return tmp_path


def shoot(c, gid, part="collar", **kw):
    return c.post(f"/garments/{gid}/photos", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
                  data={"part": part, "reason": "critical"}, **kw)


def visitor_with_photo(app):
    c = TestClient(app)
    d = c.post("/demo/session").json()
    g = c.post(f"/bundles/{d['bundle_id']}/garments", json={"garment_no": 1}).json()
    iid = shoot(c, g["id"]).json()["inspection_id"]
    return c, d, g, c.get(f"/inspections/{iid}").json()


def test_a_demo_copy_is_visible_only_to_its_owner(env):
    app, seed, _ = make_app(env)
    owner, d, g, insp = visitor_with_photo(app)
    sid, bid = d["session_id"], d["bundle_id"]
    paths = [f"/sessions/{sid}", f"/sessions/{sid}/dashboard", f"/bundles/{bid}/garments", f"/garments/{g['id']}/complete",
             f"/inspections/{insp['id']}", f"/agent-runs/{insp['agent_run_id']}", insp["crop_url"]]
    for p in paths[:1] + paths[4:]:
        assert owner.get(p).status_code == 200, p
    stranger = TestClient(app)
    stranger.post("/demo/session")
    for p in paths:
        assert stranger.get(p).status_code == 404 and stranger.post(p, json={}).status_code == 404, p
    forged = TestClient(app, cookies={"sc_visitor": owner.cookies["sc_visitor"].split(".")[0] + ".deadbeef"})
    assert forged.get(f"/sessions/{sid}").status_code == 404
    assert TestClient(app).get(f"/sessions/{sid}").status_code == 404  # no cookie at all


def test_the_seeded_day_is_read_only_for_everyone(env):
    app, seed, _ = make_app(env)
    c = TestClient(app)
    assert c.get(f"/sessions/{seed}").status_code == 200 and c.get(f"/sessions/{seed}/dashboard").status_code == 200
    gid = app.state.con.execute("SELECT g.id FROM garment g JOIN bundle b ON b.id = g.bundle_id WHERE b.session_id = ? "
                                "LIMIT 1", (seed,)).fetchone()["id"]
    assert c.get(f"/garments/{gid}/complete").status_code == 405  # the route exists only as POST, but GET is let through
    for p in (f"/sessions/{seed}/report", f"/sessions/{seed}/bundles", f"/garments/{gid}/complete", f"/sessions/{seed}/brain"):
        assert c.post(p, json={"bundle_no": 9, "brain": "open"}).status_code == 403, p


def test_unit_pin_guards_real_sessions(env, monkeypatch):
    app, seed, _ = make_app(env)
    c = TestClient(app)
    s, (g,) = start(c)  # no PIN set: real sessions are open
    assert c.get(f"/sessions/{s['id']}").status_code == 200
    monkeypatch.setenv("STITCHCHECK_UNIT_PIN", "4821")
    new = dict(date="2026-10-28", style="shirt", fabric="A", lot_size=500, line_plan=PLAN)
    assert c.post("/sessions", json=new).status_code == 401
    assert c.post("/sessions", json=new, headers={"X-Unit-Pin": "4821"}).status_code == 200
    assert c.get(f"/sessions/{s['id']}").status_code == 404
    assert c.get(f"/garments/{g['id']}/complete").status_code == 404
    assert c.get(f"/sessions/{s['id']}", headers={"X-Unit-Pin": "4821"}).status_code == 200
    assert c.get(f"/sessions/{seed}").status_code == 200  # the demo day needs no PIN
    r = c.post("/auth/pin", json={"pin": "4821"})
    assert r.status_code == 200 and "sc_unit=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert c.get(f"/sessions/{s['id']}/dashboard").status_code == 200  # by cookie
    monkeypatch.setenv("STITCHCHECK_UNIT_PIN", "9999")  # a changed PIN invalidates the cookie
    assert c.get(f"/sessions/{s['id']}").status_code == 404


def test_five_wrong_pins_lock_the_address(env, monkeypatch):
    app, _, _ = make_app(env)
    monkeypatch.setenv("STITCHCHECK_UNIT_PIN", "4821")
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/auth/pin", json={"pin": "0000"}).status_code == 401
    assert c.post("/auth/pin", json={"pin": "4821"}).status_code == 429
    s = c.post("/sessions", json=dict(date="d", style="s", fabric="A", lot_size=500, line_plan=PLAN),
               headers={"X-Unit-Pin": "4821"})
    assert s.status_code == 429


def test_agent_runs_resolve_through_inspections_voice_notes_reports_and_alerts(env, monkeypatch):
    app, _, _ = make_app(env)
    c = TestClient(app)
    s, (g,) = start(c)
    iid = shoot(c, g["id"]).json()["inspection_id"]
    v = c.post(f"/inspections/{iid}/voice", files={"audio": ("v.m4a", b"fake", "audio/mp4")}).json()
    con = app.state.con
    rep_run = db.insert(con, "agent_run", kind="report")
    db.insert(con, "report", session_id=s["id"], kind="daily", agent_run_id=rep_run)
    alert_run = db.insert(con, "agent_run", kind="alert")
    db.insert(con, "alert", session_id=s["id"], machine_id="M01", defect="skipped_stitch", garment_ids=[g["id"]],
              agent_run_id=alert_run)
    orphan = db.insert(con, "agent_run", kind="inspect")
    runs = [c.get(f"/inspections/{iid}").json()["agent_run_id"], v["agent_run_id"], rep_run, alert_run]
    for r in runs:
        assert c.get(f"/agent-runs/{r}").status_code == 200
    assert c.get(f"/agent-runs/{orphan}").status_code == 404
    monkeypatch.setenv("STITCHCHECK_UNIT_PIN", "4821")
    for r in runs:
        assert c.get(f"/agent-runs/{r}").status_code == 404
        assert c.get(f"/agent-runs/{r}", headers={"X-Unit-Pin": "4821"}).status_code == 200


def test_open_paths_stay_open(env, monkeypatch):
    app, _, _ = make_app(env)
    monkeypatch.setenv("STITCHCHECK_UNIT_PIN", "4821")
    c = TestClient(app)
    assert c.get("/health").status_code == 200 and c.get("/brains").status_code == 200
    assert c.get("/demo/gallery").json() == []
    assert c.get("/sessions/abc").status_code == 404 and c.get("/media/abc/x.jpg").status_code == 404


def test_safe_mode_pauses_live_calls_and_serves_the_last_report(env, monkeypatch):
    app, _, _ = make_app(env)
    c = TestClient(app)
    s, (g,) = start(c)
    iid = shoot(c, g["id"]).json()["inspection_id"]
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "1")
    for r in (shoot(c, g["id"]), c.post(f"/inspections/{iid}/voice", files={"audio": ("v.m4a", b"x", "audio/mp4")}),
              c.post(f"/sessions/{s['id']}/report")):
        assert r.status_code == 503 and r.json() == {"detail": PAUSED}
    rid = db.insert(app.state.con, "report", session_id=s["id"], kind="daily", body_md="## Saved", validator_ok=1)
    r = c.post(f"/sessions/{s['id']}/report")
    assert r.status_code == 200 and r.json()["id"] == rid and r.json()["body_md"] == "## Saved"
    brain = app.state.ctx.brains.available["spark"]
    with pytest.raises(RuntimeError):  # every brain client refuses while paused
        brain.client.chat.completions.create(messages=[])
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "0")  # flipped back without a restart
    assert shoot(c, g["id"]).status_code == 200


def test_admin_stats(env, monkeypatch):
    app, _, _ = make_app(env)
    c = TestClient(app)
    assert c.get("/admin/stats").status_code == 404  # admin off
    monkeypatch.setenv("STITCHCHECK_ADMIN_TOKEN", "t0ken")
    assert c.get("/admin/stats", headers={"X-Admin-Token": "nope"}).status_code == 404
    s, gs = start(c, n_garments=2)
    for g in gs:
        shoot(c, g["id"])
    visitor_with_photo(app)
    st = c.get("/admin/stats", headers={"X-Admin-Token": "t0ken"}).json()
    assert set(st) == {"spend_today_usd", "agent_runs_today", "review_errors_today", "step1_p50_s", "step1_p90_s",
                       "step2_p50_s", "step2_p90_s", "demo_copies_live", "safe_mode"}
    assert st["agent_runs_today"] == {"inspect": 3} and st["spend_today_usd"]["spark"] > 0
    assert st["spend_today_usd"]["open"] == 0 and st["review_errors_today"] == 0
    assert st["step1_p50_s"] is not None and st["step2_p90_s"] >= st["step2_p50_s"] >= 0
    assert st["demo_copies_live"] == 1 and st["safe_mode"] is False


def test_admin_stats_count_review_errors(env, monkeypatch):
    app, _, _ = make_app(env, spark=FakeSpark(fail=True))
    monkeypatch.setenv("STITCHCHECK_ADMIN_TOKEN", "t0ken")
    c = TestClient(app)
    s, (g,) = start(c)
    shoot(c, g["id"])
    monkeypatch.setenv("STITCHCHECK_SAFE_MODE", "1")
    st = c.get("/admin/stats", headers={"X-Admin-Token": "t0ken"}).json()
    assert st["review_errors_today"] == 1 and st["safe_mode"] is True
