import threading

from fastapi.testclient import TestClient
from PIL import Image

from stitchcheck.backend import db
from stitchcheck.backend.app import Deps, create_app
from test_api import PLAN, SKIPPED
from test_pipeline import FakeEnsemble, FakeSpark, fake_sam, photo_bytes

CONFIRM = {"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}
HTTP_USERS, DB_THREADS, GARMENTS = 5, 5, 5


def test_threads_share_one_connection_without_errors_or_stuck_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr("stitchcheck.spark.inspect.CACHE_DIR", tmp_path / "cache")
    ref = tmp_path / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    app = create_app(Deps(db_path=str(tmp_path / "t.db"), storage=str(tmp_path / "photos"), sam=fake_sam(),
                          ensemble=FakeEnsemble(SKIPPED), spark=FakeSpark(CONFIRM), transcribe=lambda p: "collar",
                          reference=lambda f: ref, app_dir=str(tmp_path / "no_app")))
    con, errors, start = app.state.con, [], threading.Barrier(HTTP_USERS + DB_THREADS)

    def checked(fn):
        def run(n):
            try:
                start.wait()
                fn(n)
            except Exception as ex:  # collected and asserted below
                errors.append(f"{fn.__name__} {n}: {type(ex).__name__}: {ex}")
        return run

    @checked
    def user(n):
        c = TestClient(app)
        ok = lambda r: r.json() if r.status_code == 200 else errors.append(f"user {n}: {r.status_code} {r.text}")
        s = ok(c.post("/sessions", json=dict(date="2026-10-30", style="shirt", fabric="A", lot_size=500, line_plan=PLAN)))
        b = ok(c.post(f"/sessions/{s['id']}/bundles", json={"bundle_no": 1}))
        for no in range(1, GARMENTS + 1):
            g = ok(c.post(f"/bundles/{b['id']}/garments", json={"garment_no": no}))
            r = ok(c.post(f"/garments/{g['id']}/photos", files={"image": ("p.jpg", photo_bytes(), "image/jpeg")},
                          data={"part": "collar", "reason": "critical"}))
            ok(c.get(f"/inspections/{r['inspection_id']}"))
            ok(c.post(f"/inspections/{r['inspection_id']}/decision", json={"action": "agree"}))
            ok(c.post(f"/garments/{g['id']}/complete"))
            ok(c.get(f"/sessions/{s['id']}/dashboard"))

    @checked
    def worker(n):
        sid = db.insert(con, "session", date="2026-10-30", style=f"load {n}", fabric="B", lot_size=100)
        db.insert(con, "line_plan", session_id=sid, part="collar", sequence_no=1, operator_id="OP01", machine_id="M01")
        for i in range(60):
            bid = db.insert(con, "bundle", session_id=sid, bundle_no=i + 1)
            gid = db.insert(con, "garment", bundle_id=bid, garment_no=1)
            db.update(con, "garment", gid, status="complete", completed_at="2026-10-30T10:00:00")
            assert con.execute("SELECT id FROM garment WHERE id = ?", (gid,)).fetchone()["id"] == gid
            db.session_inspections(con, 1)
            assert len(db.completed_garments(con, sid)) == i + 1
            assert len(con.execute("SELECT * FROM line_plan WHERE session_id = ?", (sid,)).fetchall()) == 1

    threads = [threading.Thread(target=user, args=(n,)) for n in range(HTTP_USERS)]
    threads += [threading.Thread(target=worker, args=(n,)) for n in range(DB_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert errors == []
    insp = con.execute("SELECT review_status, decision FROM inspection").fetchall()
    assert len(insp) == HTTP_USERS * GARMENTS
    assert all(i["review_status"] == "done" and i["decision"] == "agree" for i in insp)
