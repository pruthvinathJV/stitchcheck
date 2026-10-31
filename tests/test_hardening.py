import sqlite3

from stitchcheck.backend import db

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
