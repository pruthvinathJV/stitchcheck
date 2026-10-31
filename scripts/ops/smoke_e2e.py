"""End-to-end smoke test of a deployed StitchCheck: the whole checker journey through the public API.

    python scripts/ops/smoke_e2e.py https://jvpnath--stitchcheck-web-web.us-east.modal.run
    python scripts/ops/smoke_e2e.py URL --no-ai              no photo and no report: no model calls, no spend
    python scripts/ops/smoke_e2e.py URL --pin 1234           create a real session when demo mode is not deployed
    python scripts/ops/smoke_e2e.py URL --expect-commit abc1234   /version must report this commit

Steps: health, version, brains, demo session (or a new session), new garment, photo upload, poll the review, agree,
complete the garment, dashboard (DHU recomputed from its own by.defect counts), audit start and read, report
(validator_ok), the inspection's agent trace. Endpoints that answer 404 are reported as "not deployed yet". Prints a
table of step, result and milliseconds; exits 1 if any required step fails. One run with AI makes two model calls
(one photo review, one report).
"""
import argparse
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

PARTS = ("collar", "pocket", "hem", "side_seam", "sleeve", "placket", "cuff", "shoulder")
DEFAULT_PHOTO = Path("data/smoke/shirt/pocket.jpeg")
POLL_TIMEOUT_S = 90
SMOKE_PLAN = [{"part": p, "operator_id": f"OP{i:02d}", "machine_id": str(i)} for i, p in enumerate(PARTS, 1)]


@dataclass
class Result:
    step: str
    ok: bool | None  # None: skipped or not deployed
    ms: int = 0
    note: str = ""
    required: bool = True


# Pure helpers (tests/test_ops.py) ----------------------------------------------------------------------------------

def dhu_check(dash, tol=0.01):
    """(ok, expected, reported): the dashboard DHU against 100 * confirmed defects / completed garments, with the
    defect count summed from the dashboard's own by.defect breakdown."""
    completed = dash["completed_garments"]
    defects = sum(v["defects"] for v in dash["by"]["defect"].values())
    expected = round(100 * defects / completed, 2) if completed else 0.0
    return abs(dash["dhu"] - expected) <= tol, expected, dash["dhu"]


def part_for(photo):
    """The part a photo shows, from its file name (pocket.jpeg is a pocket); collar otherwise."""
    stem = Path(photo).stem.lower()
    return stem if stem in PARTS else "collar"


def mark(r):
    if r.ok is None:
        return "skip"
    return "ok" if r.ok else ("FAIL" if r.required else "warn")


def format_table(results):
    rows = [("step", "result", "ms", "note")] + [(r.step, mark(r), str(r.ms), r.note) for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = []
    for n, row in enumerate(rows):
        lines.append("  ".join([row[0].ljust(widths[0]), row[1].ljust(widths[1]), row[2].rjust(widths[2]), row[3]]).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def exit_code(results):
    return 1 if any(r.required and r.ok is False for r in results) else 0


# The journey -------------------------------------------------------------------------------------------------------

class Smoke:
    def __init__(self, base, http, timeout=30):
        self.base, self.http, self.timeout = base.rstrip("/"), http, timeout
        self.results = []

    def call(self, method, path, **kw):
        kw.setdefault("timeout", self.timeout)
        return self.http.request(method, self.base + path, **kw)

    def step(self, name, fn, required=True):
        """Run fn() -> (ok, note[, value]); record the time. Exceptions are failures. Returns the value (or None)."""
        t = time.perf_counter()
        try:
            out = fn()
        except Exception as ex:
            out = (False, f"{type(ex).__name__}: {ex}"[:160])
        ok, note, value = (out + (None,))[:3]
        self.results.append(Result(name, ok, round((time.perf_counter() - t) * 1000), note, required))
        print(f"  {name}: {mark(self.results[-1])} {note}", flush=True)
        return value

    def skip(self, name, why, required=True):
        self.results.append(Result(name, None, 0, why, required))


def body_note(r):
    try:
        detail = r.json().get("detail")
    except Exception:
        detail = r.text[:120]
    return f"HTTP {r.status_code}" + (f": {detail}" if detail else "")


def run(base, photo=DEFAULT_PHOTO, no_ai=False, pin=None, expect_commit=None, http=None):
    http = http or requests.Session()  # one cookie jar: the visitor cookie from /demo/session rides along
    if pin:
        http.headers["X-Unit-Pin"] = pin
    s = Smoke(base, http)

    def health():
        r = s.call("GET", "/health")
        return r.status_code == 200 and r.json().get("ok") is True, body_note(r)
    s.step("health", health)

    def version():
        r = s.call("GET", "/version")
        if r.status_code == 404:
            return None, "not deployed yet"
        body = r.json() if r.ok else {}
        commit = body.get("commit") if isinstance(body, dict) else None
        if expect_commit:
            return bool(commit) and commit.startswith(expect_commit[:7]), f"commit {commit}, expected {expect_commit}"
        return r.ok, f"commit {commit}" if r.ok else body_note(r)
    s.step("version", version, required=bool(expect_commit))

    def brains():
        r = s.call("GET", "/brains")
        if not r.ok:
            return False, body_note(r)
        avail = [b["brain"] for b in r.json() if b.get("available")]
        return (bool(avail) or no_ai), "available: " + (", ".join(avail) or "none")
    s.step("brains", brains)

    def session():
        r = s.call("POST", "/demo/session")
        if r.status_code == 200:
            d = r.json()
            return True, f"demo session {d['session_id']}", (d["session_id"], d["bundle_id"])
        note = "demo not deployed yet; " if r.status_code == 404 else f"demo {body_note(r)}; "
        r = s.call("POST", "/sessions", json=dict(date=time.strftime("%Y-%m-%d"), style="Smoke test", fabric="A",
                                                  lot_size=500, line_plan=SMOKE_PLAN))
        if r.status_code in (401, 403):
            return False, note + f"new session refused ({body_note(r)}); pass --pin"
        if not r.ok:
            return False, note + "new session " + body_note(r)
        sid = r.json()["id"]
        b = s.call("POST", f"/sessions/{sid}/bundles", json={"bundle_no": random.randint(1, 9999)})
        if not b.ok:
            return False, note + "bundle " + body_note(b)
        return True, note + f"new session {sid}", (sid, b.json()["id"])
    ids = s.step("session", session)
    if not ids:
        for name in ("garment", "photo", "review", "agree", "complete", "dashboard_dhu", "audit_start", "audit_read",
                     "report", "trace"):
            s.skip(name, "needs a session")
        return s.results
    sid, bid = ids

    def garment():
        r = s.call("POST", f"/bundles/{bid}/garments", json={"garment_no": random.randint(100000, 999999)})
        return r.ok, (f"garment {r.json()['id']}" if r.ok else body_note(r)), (r.json()["id"] if r.ok else None)
    gid = s.step("garment", garment)

    insp = None
    if no_ai:
        for name in ("photo", "review", "agree"):
            s.skip(name, "--no-ai")
    elif not gid:
        for name in ("photo", "review", "agree"):
            s.skip(name, "needs a garment")
    else:
        def upload():
            part = part_for(photo)
            with open(photo, "rb") as f:
                r = s.call("POST", f"/garments/{gid}/photos", files={"image": (Path(photo).name, f, "image/jpeg")},
                           data={"part": part, "reason": "critical"}, timeout=120)
            if not r.ok:
                return False, body_note(r)
            d = r.json()
            if "retake" in d:
                return False, f"retake: {d['retake']}"
            return True, f"{part}: classifier {d['classifier_label']}, step 1 {d['step1_seconds']} s", d["inspection_id"]
        iid = s.step("photo", upload)

        def review():
            deadline = time.time() + POLL_TIMEOUT_S
            while True:
                r = s.call("GET", f"/inspections/{iid}")
                if not r.ok:
                    return False, body_note(r)
                d = r.json()
                if d["review_status"] == "done":
                    v = d.get("spark_verdict") or {}
                    return True, f"{d['proposed_label']} by {v.get('brain') or 'classifier only'}", d
                if time.time() > deadline:
                    return False, f"still reviewing after {POLL_TIMEOUT_S} s"
                time.sleep(1)
        insp = s.step("review", review) if iid else s.skip("review", "needs a photo")

        def agree():
            r = s.call("POST", f"/inspections/{iid}/decision", json={"action": "agree", "via": "tap"})
            return r.ok and r.json().get("decision") == "agree", (f"final {r.json().get('final_label')}" if r.ok else body_note(r))
        s.step("agree", agree) if insp else s.skip("agree", "needs a review")

    def complete():
        r = s.call("POST", f"/garments/{gid}/complete")
        return r.ok and r.json().get("status") == "complete", (f"{len(r.json().get('new_alerts', []))} new alerts" if r.ok else body_note(r))
    s.step("complete", complete) if gid else s.skip("complete", "needs a garment")

    def dashboard():
        r = s.call("GET", f"/sessions/{sid}/dashboard")
        if not r.ok:
            return False, body_note(r)
        ok, expected, got = dhu_check(r.json())
        return ok, f"DHU {got}, recomputed {expected} over {r.json()['completed_garments']} garments"
    s.step("dashboard_dhu", dashboard)

    def audit_start():
        r = s.call("POST", f"/sessions/{sid}/audit")
        return r.ok and "sampled_garment_nos" in r.json(), (f"{len(r.json()['sampled_garment_nos'])} garments sampled" if r.ok else body_note(r))
    s.step("audit_start", audit_start)

    def audit_read():
        r = s.call("GET", f"/sessions/{sid}/audit")
        return r.ok and "status" in r.json(), (f"status {r.json()['status']}" if r.ok else body_note(r))
    s.step("audit_read", audit_read)

    def gallery():
        r = s.call("GET", "/demo/gallery")
        if r.status_code == 404:
            return None, "not deployed yet"
        n = len(r.json()) if r.ok and isinstance(r.json(), list) else "?"
        return r.ok, f"{n} photos" if r.ok else body_note(r)
    s.step("gallery", gallery, required=False)

    if no_ai:
        s.skip("report", "--no-ai")
    else:
        def report():
            r = s.call("POST", f"/sessions/{sid}/report", timeout=180)
            if not r.ok:
                return False, body_note(r)
            d = r.json()
            return d.get("validator_ok") == 1, f"validator_ok {d.get('validator_ok')}, errors {d.get('errors') or 'none'}"[:160]
        s.step("report", report)

    if insp and insp.get("agent_run_id"):
        def trace():
            r = s.call("GET", f"/agent-runs/{insp['agent_run_id']}")
            if not r.ok:
                return False, body_note(r)
            d = r.json()
            calls = [st["call"]["name"] for st in d.get("steps", [])]
            return isinstance(d.get("steps"), list), f"{d.get('brain')}: {', '.join(calls) or 'no tool calls'}, {d.get('latency_ms')} ms"
        s.step("trace", trace)
    else:
        s.skip("trace", "--no-ai" if no_ai else "needs a reviewed inspection")
    return s.results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--photo", type=Path, default=DEFAULT_PHOTO)
    ap.add_argument("--no-ai", action="store_true", help="skip the photo and the report (no model calls)")
    ap.add_argument("--pin", help="unit PIN, sent as X-Unit-Pin")
    ap.add_argument("--expect-commit", help="fail unless /version reports this commit")
    a = ap.parse_args(argv)
    if not a.no_ai and not a.photo.exists():
        ap.error(f"photo not found: {a.photo} (pass --photo PATH or --no-ai)")
    print(f"smoke test against {a.url}", flush=True)
    t = time.perf_counter()
    results = run(a.url, photo=a.photo, no_ai=a.no_ai, pin=a.pin, expect_commit=a.expect_commit)
    print()
    print(format_table(results))
    code = exit_code(results)
    print(f"\n{'PASS' if code == 0 else 'FAIL'} in {time.perf_counter() - t:.1f} s")
    return code


if __name__ == "__main__":
    sys.exit(main())
