"""SQLite storage for the app (docs/backend_spec.md, section 1). One writer: the app runs in a single container."""
import json
import re
import sqlite3
import threading
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
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT,
    path TEXT,
    status INTEGER,  -- 0 while the first request with this key is still running
    body TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (key, path)
);
CREATE INDEX IF NOT EXISTS inspection_garment ON inspection(garment_id);
CREATE INDEX IF NOT EXISTS garment_bundle ON garment(bundle_id);
"""
# Values for a column added to an older database, applied to the rows already there (not a default for new rows)
BACKFILL = {("inspection", "review_status"): "'done'"}  # before the two-step review, every review finished in one call
JSON_COLUMNS = {"critical_parts", "classifier_top2", "spark_verdict", "timings", "parsed", "plan", "sampled_garment_nos",
                "garment_ids", "request", "tool_calls", "tool_outputs", "final"}


class Rows:
    """The result of Connection.execute: rows fetched while the lock was held, read like a cursor."""

    def __init__(self, rows, lastrowid, description, rowcount):
        self.rows, self.lastrowid, self.description, self.rowcount = rows, lastrowid, description, rowcount
        self._next = 0

    def fetchone(self):
        if self._next >= len(self.rows):
            return None
        self._next += 1
        return self.rows[self._next - 1]

    def fetchall(self):
        rest, self._next = self.rows[self._next:], len(self.rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    """One sqlite3 connection shared by the server's threads. Every call holds one re-entrant lock (`lock`, also the
    app's writer lock) and fetches its rows before releasing it, so threads never interleave on the connection."""

    def __init__(self, raw):
        self.raw, self.lock = raw, threading.RLock()

    def execute(self, sql, params=()):
        with self.lock:
            cur = self.raw.execute(sql, params)
            return Rows(cur.fetchall(), cur.lastrowid, cur.description, cur.rowcount)

    def executescript(self, script):
        with self.lock:
            self.raw.executescript(script)

    def commit(self):
        with self.lock:
            self.raw.commit()

    def rollback(self):
        with self.lock:
            self.raw.rollback()

    def close(self):
        with self.lock:
            self.raw.close()

    def __getattr__(self, name):
        return getattr(self.raw, name)


def connect(path):
    """Open (and create if needed) the database. Rows come back as dicts with JSON columns decoded. The connection
    is safe to share between threads (Connection)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, check_same_thread=False)
    raw.row_factory = _row
    con = Connection(raw)
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    con.executescript(SCHEMA)
    return con


def migrate(con):
    """Add the columns SCHEMA has and an older database lacks, keeping every row. SQLite cannot add a NOT NULL
    column without a constant default, so such columns are added nullable; a non-constant default such as
    datetime('now') is then filled in for new rows by an insert trigger."""
    ref = sqlite3.connect(":memory:")
    ref.executescript(SCHEMA)
    for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if not have:
            continue  # a new table: created by SCHEMA
        for _, name, kind, notnull, default, pk in ref.execute(f"PRAGMA table_info({table})").fetchall():
            if name in have or pk:
                continue
            constant = default is None or _constant(default)
            col = f"{name} {kind}"
            if default is not None and constant:
                col += f" DEFAULT {default}" + (" NOT NULL" if notnull else "")
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            if (table, name) in BACKFILL:
                con.execute(f"UPDATE {table} SET {name} = {BACKFILL[table, name]}")
            if not constant:
                con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{name}_default AFTER INSERT ON {table} "
                            f"WHEN NEW.{name} IS NULL BEGIN UPDATE {table} SET {name} = {default} "
                            f"WHERE rowid = NEW.rowid; END")
    con.commit()
    ref.close()


def _constant(default):
    return re.fullmatch(r"'(?:[^']|'')*'|[-+]?\d+(\.\d+)?|NULL|TRUE|FALSE", default, re.I) is not None


def _row(cursor, values):
    row = {}
    for (name, *_), v in zip(cursor.description, values):
        row[name] = json.loads(v) if name in JSON_COLUMNS and isinstance(v, str) else v
    return row


def insert(con, table, **fields):
    """Insert one row; dicts and lists are stored as JSON. Returns the new id."""
    cols = list(fields)
    vals = [json.dumps(v) if isinstance(v, (dict, list, tuple)) else v for v in fields.values()]
    with con.lock:
        cur = _write(con, f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
    return cur.lastrowid


def update(con, table, row_id, **fields):
    sets = ", ".join(f"{c} = ?" for c in fields)
    vals = [json.dumps(v) if isinstance(v, (dict, list, tuple)) else v for v in fields.values()]
    with con.lock:
        _write(con, f"UPDATE {table} SET {sets} WHERE id = ?", [*vals, row_id])


def _write(con, sql, vals):
    """One statement and its commit; a failed statement leaves no transaction open."""
    try:
        cur = con.execute(sql, vals)
        con.commit()
        return cur
    except sqlite3.Error:
        con.rollback()
        raise


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
