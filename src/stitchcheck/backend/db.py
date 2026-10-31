"""SQLite storage for the app (docs/backend_spec.md, section 1). One writer: the app runs in a single container."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    style TEXT NOT NULL,
    fabric TEXT NOT NULL,
    lot_size INTEGER NOT NULL CHECK (lot_size >= 2),
    critical_parts TEXT NOT NULL DEFAULT '["collar", "placket"]',
    spot_check_rate REAL NOT NULL DEFAULT 0.1,
    is_demo INTEGER NOT NULL DEFAULT 0,
    brain TEXT NOT NULL DEFAULT 'spark' CHECK (brain IN ('spark', 'open')),
    owner TEXT,  -- the visitor that owns a demo copy (signed cookie id); NULL for real and seeded sessions
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
);
CREATE TABLE IF NOT EXISTS line_plan (
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    part TEXT NOT NULL CHECK (part IN ('collar', 'pocket', 'hem', 'side_seam', 'sleeve', 'placket', 'cuff', 'shoulder')),
    sequence_no INTEGER NOT NULL,
    operator_id TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    PRIMARY KEY (session_id, part)
);
CREATE TABLE IF NOT EXISTS bundle (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    bundle_no INTEGER NOT NULL,
    UNIQUE (session_id, bundle_no)
);
CREATE TABLE IF NOT EXISTS garment (
    id INTEGER PRIMARY KEY,
    bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
    garment_no INTEGER NOT NULL,
    spot_check_part TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'complete')),
    completed_at TEXT,
    UNIQUE (bundle_id, garment_no)
);
CREATE TABLE IF NOT EXISTS agent_run (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('inspect', 'voice', 'report', 'alert')),
    brain TEXT,
    fell_back INTEGER NOT NULL DEFAULT 0,
    request TEXT,
    tool_calls TEXT,
    tool_outputs TEXT,
    final TEXT,
    latency_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS inspection (
    id INTEGER PRIMARY KEY,
    garment_id INTEGER NOT NULL REFERENCES garment(id) ON DELETE CASCADE,
    reason TEXT NOT NULL CHECK (reason IN ('critical', 'suspicious', 'spot_check', 'audit')),
    photo_path TEXT NOT NULL,
    crop_path TEXT,
    overlay_path TEXT,
    review_status TEXT NOT NULL DEFAULT 'reviewing' CHECK (review_status IN ('reviewing', 'done')),
    scale_path TEXT CHECK (scale_path IN ('pitch', 'fixed', 'provisional') OR scale_path IS NULL),
    part_chip TEXT,
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
    final_severity TEXT CHECK (final_severity IN ('critical', 'major', 'minor', 'none') OR final_severity IS NULL),
    operator_id TEXT,
    machine_id TEXT,
    decision TEXT NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'agree', 'changed', 'not_a_defect', 'retake')),
    decided_via TEXT CHECK (decided_via IN ('tap', 'voice') OR decided_via IS NULL),
    config_hash TEXT NOT NULL,
    agent_run_id INTEGER REFERENCES agent_run(id),
    timings TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS voice_note (
    id INTEGER PRIMARY KEY,
    inspection_id INTEGER NOT NULL REFERENCES inspection(id) ON DELETE CASCADE,
    audio_path TEXT NOT NULL,
    transcript TEXT,
    parsed TEXT,
    confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    lot_size INTEGER NOT NULL,
    plan TEXT NOT NULL,
    sampled_garment_nos TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'accept', 'reject'))
);
CREATE TABLE IF NOT EXISTS alert (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    machine_id TEXT NOT NULL,
    defect TEXT NOT NULL,
    garment_ids TEXT NOT NULL,
    body_md TEXT,
    agent_run_id INTEGER REFERENCES agent_run(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS report (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('daily', 'audit')),
    body_template TEXT,
    body_md TEXT,
    validator_ok INTEGER,
    agent_run_id INTEGER REFERENCES agent_run(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS inspection_garment ON inspection(garment_id);
CREATE INDEX IF NOT EXISTS garment_bundle ON garment(bundle_id);
"""
JSON_COLUMNS = {"critical_parts", "classifier_top2", "spark_verdict", "timings", "parsed", "plan", "sampled_garment_nos",
                "garment_ids", "request", "tool_calls", "tool_outputs", "final"}


def connect(path):
    """Open (and create if needed) the database. Rows come back as dicts with JSON columns decoded."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = _row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def _row(cursor, values):
    row = {}
    for (name, *_), v in zip(cursor.description, values):
        row[name] = json.loads(v) if name in JSON_COLUMNS and isinstance(v, str) else v
    return row


def insert(con, table, **fields):
    """Insert one row; dicts and lists are stored as JSON. Returns the new id."""
    cols = list(fields)
    vals = [json.dumps(v) if isinstance(v, (dict, list, tuple)) else v for v in fields.values()]
    cur = con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    con.commit()
    return cur.lastrowid


def update(con, table, row_id, **fields):
    sets = ", ".join(f"{c} = ?" for c in fields)
    vals = [json.dumps(v) if isinstance(v, (dict, list, tuple)) else v for v in fields.values()]
    con.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*vals, row_id])
    con.commit()


AUDIT_BUNDLE = 0  # audit garments live in bundle 0, apart from the end-line count


def session_inspections(con, session_id, audit=False):
    """Inspections of a session with their garment and bundle numbers: end-line ones by default, or the audit's."""
    op = "=" if audit else "!="
    return con.execute(
        "SELECT i.*, g.garment_no, g.status AS garment_status, b.bundle_no FROM inspection i "
        "JOIN garment g ON g.id = i.garment_id JOIN bundle b ON b.id = g.bundle_id "
        f"WHERE b.session_id = ? AND b.bundle_no {op} ? ORDER BY i.id", (session_id, AUDIT_BUNDLE)).fetchall()


def completed_garments(con, session_id, audit=False):
    """Completed garment ids of a session (end-line by default, or the audit's), oldest completion first."""
    op = "=" if audit else "!="
    rows = con.execute(
        "SELECT g.id FROM garment g JOIN bundle b ON b.id = g.bundle_id WHERE b.session_id = ? AND g.status = 'complete' "
        f"AND b.bundle_no {op} ? ORDER BY g.completed_at, g.id", (session_id, AUDIT_BUNDLE)).fetchall()
    return [r["id"] for r in rows]
