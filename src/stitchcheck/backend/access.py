"""Who may see which session, the unit PIN, safe mode and the admin stats (one extension: install(app, ctx)).

Rules, applied by one HTTP middleware to every path that names a session's data (/sessions/{id}, /bundles/{id},
/garments/{id}, /inspections/{id}, /reports/{id}, /agent-runs/{id}, /media/{session_id}/...):
- a visitor's demo copy (is_demo = 1, owner set) is visible only to the visitor whose signed cookie owns it;
- the seeded demo session (is_demo = 1, no owner) is read-only for everyone;
- a real session (is_demo = 0) needs the unit PIN when STITCHCHECK_UNIT_PIN is set (header X-Unit-Pin, or the
  sc_unit cookie set by POST /auth/pin); without the variable (local development) real sessions are open.
Someone else's session answers 404, never 403, so ids cannot be enumerated. /admin/* needs X-Admin-Token equal to
STITCHCHECK_ADMIN_TOKEN (unset: admin is off and answers 404).

Safe mode (STITCHCHECK_SAFE_MODE=1, read on every request) pauses live model calls: photos and voice answer 503,
a report request answers with the last saved report, and every brain client refuses calls (so a work order or a
review that is not cached is not sent either).
"""
import hashlib
import hmac
import os
import re
import secrets
import statistics
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

VISITOR_COOKIE, UNIT_COOKIE = "sc_visitor", "sc_unit"
PIN_TRIES, PIN_WINDOW = 5, 600  # wrong PIN attempts per IP per 10 minutes before 429
PAUSED = "Live AI paused for the demo; try the gallery"
_SCOPED = re.compile(r"^/(sessions|bundles|garments|inspections|reports|agent-runs|media)/([^/]+)")
READ = ("GET", "HEAD")


# ---- shared helpers (also used by demo_api) -------------------------------------------------------------------

def safe_mode():
    return os.environ.get("STITCHCHECK_SAFE_MODE", "") == "1"


def secret(app):
    """The signing secret: STITCHCHECK_SECRET, or a random one made once per process."""
    if getattr(app.state, "sc_secret", None) is None:
        app.state.sc_secret = (os.environ.get("STITCHCHECK_SECRET") or secrets.token_hex(32)).encode()
    return app.state.sc_secret


def _sign(key, value):
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def make_visitor(app):
    """A new visitor id and its cookie value (id.signature)."""
    vid = secrets.token_urlsafe(16)
    return vid, f"{vid}.{_sign(secret(app), 'visitor:' + vid)}"


def visitor_id(request):
    """The visitor id from a valid sc_visitor cookie, or None."""
    raw = request.cookies.get(VISITOR_COOKIE, "")
    vid, _, sig = raw.rpartition(".")
    if vid and hmac.compare_digest(sig, _sign(secret(request.app), "visitor:" + vid)):
        return vid
    return None


def is_https(request):
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def client_ip(request):
    return request.client.host if request.client else "?"


# ---- the extension ---------------------------------------------------------------------------------------------

class Pin(BaseModel):
    pin: str


def install(app, ctx):
    con = ctx.con
    failures = defaultdict(deque)  # ip -> times of wrong PIN attempts

    def unit_pin():
        return os.environ.get("STITCHCHECK_UNIT_PIN") or None

    def locked(ip):
        q, now = failures[ip], time.time()
        while q and now - q[0] > PIN_WINDOW:
            q.popleft()
        return len(q) >= PIN_TRIES

    def pin_ok(request, pin):
        """True if the request carries the unit PIN (header or cookie). A wrong header counts as a failed try."""
        cookie = request.cookies.get(UNIT_COOKIE)
        if cookie and hmac.compare_digest(cookie, _sign(secret(app), "unit:" + pin)):
            return True
        header = request.headers.get("x-unit-pin")
        if header is None:
            return False
        if hmac.compare_digest(header.encode(), pin.encode()):
            return True
        failures[client_ip(request)].append(time.time())
        return False

    def owning_sessions(kind, key):
        """Session ids that own the resource, or [] when it cannot be resolved."""
        if not key.isdigit():
            return []
        k = int(key)
        q = {
            "sessions": "SELECT id AS sid FROM session WHERE id = ?",
            "media": "SELECT id AS sid FROM session WHERE id = ?",
            "bundles": "SELECT session_id AS sid FROM bundle WHERE id = ?",
            "garments": "SELECT b.session_id AS sid FROM garment g JOIN bundle b ON b.id = g.bundle_id WHERE g.id = ?",
            "inspections": "SELECT b.session_id AS sid FROM inspection i JOIN garment g ON g.id = i.garment_id "
                           "JOIN bundle b ON b.id = g.bundle_id WHERE i.id = ?",
            "reports": "SELECT session_id AS sid FROM report WHERE id = ?",
        }.get(kind)
        if q:
            return [r["sid"] for r in con.execute(q, (k,)).fetchall()]
        # agent runs: through the inspection, report, alert or voice note that points at the run
        rows = con.execute(
            "SELECT b.session_id AS sid FROM inspection i JOIN garment g ON g.id = i.garment_id JOIN bundle b ON b.id = g.bundle_id "
            "WHERE i.agent_run_id = ?1 UNION SELECT session_id FROM report WHERE agent_run_id = ?1 "
            "UNION SELECT session_id FROM alert WHERE agent_run_id = ?1 "
            "UNION SELECT b.session_id FROM voice_note v JOIN inspection i ON i.id = v.inspection_id "
            "JOIN garment g ON g.id = i.garment_id JOIN bundle b ON b.id = g.bundle_id "
            "WHERE json_valid(v.parsed) AND json_extract(v.parsed, '$.agent_run_id') = ?1", (k,)).fetchall()
        return [r["sid"] for r in rows]

    def verdict(request, sid):
        """None if allowed, else the status code to answer with (404 hides someone else's session)."""
        s = con.execute("SELECT is_demo, owner FROM session WHERE id = ?", (sid,)).fetchone()
        if s is None:
            return 404
        if s["is_demo"] and s["owner"] is not None:
            vid = visitor_id(request)
            return None if vid is not None and hmac.compare_digest(vid, s["owner"]) else 404
        if s["is_demo"]:
            return None if request.method in READ else 403
        pin = unit_pin()
        if pin is None:
            return None
        if locked(client_ip(request)):
            return 429
        return None if pin_ok(request, pin) else 404

    def deny(code):
        detail = {403: "the demo day is read-only; start your own copy",
                  404: "not found", 429: "too many wrong PIN attempts; try again later",
                  401: "unit PIN required"}[code]
        return JSONResponse({"detail": detail}, status_code=code)

    @app.middleware("http")
    async def safe_mode_guard(request: Request, call_next):
        """Inside the access check: by the time this runs, the caller may see the session."""
        if request.method == "POST" and safe_mode():
            path = request.url.path
            if re.fullmatch(r"/garments/[^/]+/photos|/inspections/[^/]+/voice", path):
                return JSONResponse({"detail": PAUSED}, status_code=503)
            m = re.fullmatch(r"/sessions/(\d+)/report", path)
            if m:
                r = con.execute("SELECT * FROM report WHERE session_id = ? ORDER BY id DESC LIMIT 1", (int(m[1]),)).fetchone()
                return JSONResponse(r | {"errors": []}) if r else JSONResponse({"detail": PAUSED}, status_code=503)
        return await call_next(request)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        path = request.url.path
        if path.startswith("/admin/") or path == "/admin":
            token = os.environ.get("STITCHCHECK_ADMIN_TOKEN")
            given = request.headers.get("x-admin-token", "")
            if not token or not hmac.compare_digest(given.encode(), token.encode()):
                return deny(404)
            return await call_next(request)
        if path.rstrip("/") == "/sessions" and request.method == "POST":
            pin = unit_pin()
            if pin is not None:
                if locked(client_ip(request)):
                    return deny(429)
                if not pin_ok(request, pin):
                    return deny(401)
            return await call_next(request)
        m = _SCOPED.match(path)
        if m:
            sids = owning_sessions(m[1], m[2])
            if not sids:
                return deny(404)
            codes = [verdict(request, sid) for sid in sids]
            if None not in codes:
                return deny(403 if 403 in codes else 429 if 429 in codes else 404)
        return await call_next(request)

    @app.post("/auth/pin")
    def auth_pin(p: Pin, request: Request):
        pin = unit_pin()
        if pin is None:
            return {"ok": True, "pin_required": False}
        ip = client_ip(request)
        if locked(ip):
            raise HTTPException(429, "too many wrong PIN attempts; try again later")
        if not hmac.compare_digest(p.pin.encode(), pin.encode()):
            failures[ip].append(time.time())
            raise HTTPException(401, "wrong PIN")
        resp = JSONResponse({"ok": True, "pin_required": True})
        resp.set_cookie(UNIT_COOKIE, _sign(secret(app), "unit:" + pin), max_age=30 * 86400, httponly=True,
                        samesite="lax", secure=is_https(request))
        return resp

    @app.get("/admin/stats")
    def admin_stats():
        today = "date(created_at) = date('now')"
        spend = {r["brain"]: r["usd"] for r in con.execute(
            f"SELECT brain, COALESCE(SUM(cost_usd), 0) AS usd FROM agent_run WHERE {today} GROUP BY brain").fetchall()}
        runs = {r["kind"]: r["n"] for r in con.execute(
            f"SELECT kind, COUNT(*) AS n FROM agent_run WHERE {today} GROUP BY kind").fetchall()}
        insp = con.execute(f"SELECT spark_verdict, timings FROM inspection WHERE {today}").fetchall()
        errors = sum(1 for i in insp if isinstance(i["spark_verdict"], dict) and i["spark_verdict"].get("spark_error"))
        out = {"spend_today_usd": {"spark": round(spend.get("spark", 0.0), 4), "open": round(spend.get("open", 0.0), 4)},
               "agent_runs_today": runs, "review_errors_today": errors}
        for step in ("step1", "step2"):
            xs = [float(i["timings"][step]) for i in insp if isinstance(i["timings"], dict)
                  and isinstance(i["timings"].get(step), (int, float))]
            out[f"{step}_p50_s"], out[f"{step}_p90_s"] = _quantile(xs, 0.5), _quantile(xs, 0.9)
        out["demo_copies_live"] = con.execute(
            "SELECT COUNT(*) AS n FROM session WHERE is_demo = 1 AND owner IS NOT NULL "
            "AND created_at >= datetime('now', '-24 hours')").fetchone()["n"]
        out["safe_mode"] = safe_mode()
        return out

    _guard_brains(ctx.brains)


def _quantile(xs, q):
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 2)
    return round(statistics.quantiles(xs, n=100, method="inclusive")[int(q * 100) - 1], 2)


class _Paused:
    """Wraps a model client: every call reached through it raises while safe mode is on."""

    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        return _Paused(getattr(self._target, name))

    def __call__(self, *a, **k):
        if safe_mode():
            raise RuntimeError(PAUSED)
        return self._target(*a, **k)


def _guard_brains(brains):
    for b in brains.available.values():
        if not isinstance(b.client, _Paused):
            b.client = _Paused(b.client)
