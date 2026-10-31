"""Local load test of the StitchCheck API: no network beyond localhost and no model calls.

    python scripts/ops/load_test.py                       20 virtual users x 5 garments x 2 photos
    python scripts/ops/load_test.py --users 40 --garments 10

The app is built with create_app() and the fakes from tests/test_pipeline.py (fake SAM, classifier and Spark) on a
temporary database, and served by uvicorn on a free port in a background thread. Each virtual user (a thread with its
own client address, so the per-IP limit applies per user as on a real line) starts a session and a bundle, then for
every garment: new garment, two photos (collar and placket), polls each review, decides (agree, change, not a
defect in turn), completes the garment and reads the dashboard. Prints p50/p95 latency per endpoint and the error
count; exits 1 on any 5xx, connection error, stuck review or "database is locked" (in a response or the server log).
"""
import argparse
import logging
import math
import re
import socket
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = [{"part": "collar", "operator_id": "OP01", "machine_id": "7"},
        {"part": "placket", "operator_id": "OP02", "machine_id": "12"},
        {"part": "cuff", "operator_id": "OP03", "machine_id": "3"}]
SKIPPED = [0.05, 0.85, 0.05, 0.03, 0.02]  # good, skipped, broken, crooked, stain
DECISIONS = [{"action": "agree"}, {"action": "change", "label": "broken_stitch"}, {"action": "not_a_defect"}]
REVIEW_TIMEOUT_S = 30


@dataclass
class Rec:
    endpoint: str
    ms: float
    status: int | None  # None: no response (connection error, timeout)
    error: str = ""


# Pure helpers (tests/test_ops.py) ----------------------------------------------------------------------------------

def endpoint_of(method, path):
    """'POST /garments/12/photos' -> 'POST /garments/{id}/photos'."""
    return f"{method} " + re.sub(r"/\d+(?=/|$)", "/{id}", path.split("?")[0])


def percentile(values, q):
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    v = sorted(values)
    k = min(len(v) - 1, max(0, math.ceil(q / 100 * len(v)) - 1))
    return v[k]


def is_error(rec):
    return rec.status is None or rec.status >= 400 or bool(rec.error)


def summarize(recs):
    """{endpoint: {n, p50, p95, max, errors}} sorted by endpoint."""
    by = defaultdict(list)
    for r in recs:
        by[r.endpoint].append(r)
    return {ep: {"n": len(rs), "p50": percentile([r.ms for r in rs], 50), "p95": percentile([r.ms for r in rs], 95),
                 "max": max(r.ms for r in rs), "errors": sum(is_error(r) for r in rs)} for ep, rs in sorted(by.items())}


def must_fail(recs, server_log):
    """Reasons to exit 1: any 5xx, a request without a response, a stuck review, or 'database is locked'."""
    reasons = []
    five = sum(1 for r in recs if r.status is not None and r.status >= 500)
    if five:
        reasons.append(f"{five} responses with 5xx")
    lost = sum(1 for r in recs if r.status is None)
    if lost:
        reasons.append(f"{lost} requests without a response")
    stuck = sum(1 for r in recs if "still reviewing" in r.error)
    if stuck:
        reasons.append(f"{stuck} reviews never finished")
    locked = sum("database is locked" in r.error for r in recs) + sum("database is locked" in m for m in server_log)
    if locked:
        reasons.append(f"'database is locked' seen {locked} times")
    return reasons


def format_summary(summary):
    rows = [("endpoint", "n", "p50 ms", "p95 ms", "max ms", "errors")]
    rows += [(ep, str(s["n"]), f"{s['p50']:.0f}", f"{s['p95']:.0f}", f"{s['max']:.0f}", str(s["errors"]))
             for ep, s in summary.items()]
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join([r[0].ljust(w[0])] + [c.rjust(w[i + 1]) for i, c in enumerate(r[1:])]) for r in rows)


# The server --------------------------------------------------------------------------------------------------------

class LogTap(logging.Handler):
    """Collects every server-side error (with its traceback text) so background-task failures count too."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.messages = []

    def emit(self, record):
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.messages.append(text)


def build_app(tmp):
    for p in (ROOT / "src", ROOT / "tests"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from PIL import Image

    import stitchcheck.spark.inspect as hybrid
    from stitchcheck.backend.app import Deps, create_app
    from test_pipeline import FakeEnsemble, FakeSpark, fake_sam

    hybrid.CACHE_DIR = Path(tmp) / "cache"  # never touch the real Spark cache
    ref = Path(tmp) / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    deps = Deps(db_path=str(Path(tmp) / "load.db"), storage=str(Path(tmp) / "photos"),
                sam=fake_sam(parts={"pocket": 0.9}), ensemble=FakeEnsemble(SKIPPED),
                spark=FakeSpark({"verdict": "confirm", "label": "skipped_stitch", "reason": "long stitch"}),
                transcribe=lambda path: "", reference=lambda fabric: ref)
    return create_app(deps)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, tap=None):
    """Start uvicorn in a daemon thread; returns (server, base_url). X-Forwarded-For sets the client address. With a
    tap, server errors go to it instead of the console."""
    import uvicorn
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", proxy_headers=True,
                            forwarded_allow_ips="*", access_log=False)  # configures logging, so the tap goes in after
    if tap:
        for name in ("uvicorn", "uvicorn.error"):
            logging.getLogger(name).handlers = [tap]
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    return server, f"http://127.0.0.1:{port}"


# The virtual users -------------------------------------------------------------------------------------------------

def photo_bytes():
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 200, 210)).save(buf, format="JPEG")  # matches the fake SAM's seam mask
    return buf.getvalue()


def virtual_user(base, n, garments, photos, image, recs, lock):
    import requests
    http = requests.Session()
    http.headers["X-Forwarded-For"] = f"10.{n // 250}.{n % 250}.1"

    def call(method, path, **kw):
        t = time.perf_counter()
        try:
            r = http.request(method, base + path, timeout=60, **kw)
            err = r.text[:300] if r.status_code >= 400 else ""
            rec = Rec(endpoint_of(method, path), (time.perf_counter() - t) * 1000, r.status_code, err)
        except Exception as ex:
            r, rec = None, Rec(endpoint_of(method, path), (time.perf_counter() - t) * 1000, None, f"{type(ex).__name__}: {ex}")
        with lock:
            recs.append(rec)
        return r if r is not None and r.ok else None

    def note(endpoint, error):
        with lock:
            recs.append(Rec(endpoint, 0, 200, error))

    s = call("POST", "/sessions", json=dict(date="2026-11-05", style="Load test", fabric="A", lot_size=500, line_plan=PLAN))
    if not s:
        return
    sid = s.json()["id"]
    b = call("POST", f"/sessions/{sid}/bundles", json={"bundle_no": 1})
    if not b:
        return
    for g in range(1, garments + 1):
        gr = call("POST", f"/bundles/{b.json()['id']}/garments", json={"garment_no": g})
        if not gr:
            continue
        gid = gr.json()["id"]
        for k, part in enumerate(["collar", "placket", "cuff"][:photos]):
            p = call("POST", f"/garments/{gid}/photos", files={"image": ("p.jpg", image, "image/jpeg")},
                     data={"part": part, "reason": "critical"})
            if not p or "inspection_id" not in p.json():
                continue
            iid = p.json()["inspection_id"]
            deadline = time.time() + REVIEW_TIMEOUT_S
            while True:
                i = call("GET", f"/inspections/{iid}")
                if i is None or i.json()["review_status"] == "done":
                    break
                if time.time() > deadline:
                    note("GET /inspections/{id}", f"still reviewing after {REVIEW_TIMEOUT_S} s")
                    break
                time.sleep(0.2)
            call("POST", f"/inspections/{iid}/decision", json=DECISIONS[(g + k) % len(DECISIONS)] | {"via": "tap"})
        call("POST", f"/garments/{gid}/complete")
        call("GET", f"/sessions/{sid}/dashboard")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--garments", type=int, default=5, help="garments per user")
    ap.add_argument("--photos", type=int, default=2, choices=(1, 2, 3), help="photos per garment")
    a = ap.parse_args(argv)

    tap = LogTap()
    logging.getLogger().addHandler(tap)
    with tempfile.TemporaryDirectory() as tmp:
        server, base = serve(build_app(tmp), tap)
        recs, lock, image = [], threading.Lock(), photo_bytes()
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=a.users) as pool:
            for f in [pool.submit(virtual_user, base, n, a.garments, a.photos, image, recs, lock) for n in range(a.users)]:
                f.result()
        wall = time.perf_counter() - t
        server.should_exit = True
        time.sleep(0.3)

    summary = summarize(recs)
    errors = sum(is_error(r) for r in recs)
    print(f"{a.users} users x {a.garments} garments x {a.photos} photos: {len(recs)} requests in {wall:.1f} s "
          f"({len(recs) / wall:.0f} req/s)\n")
    print(format_summary(summary))
    print(f"\nerrors: {errors}; server log errors: {len(tap.messages)}")
    for (status, text), n in Counter((r.status, r.error.splitlines()[-1][:120] if r.error else "")
                                     for r in recs if is_error(r)).most_common(8):
        print(f"  {n:4d} x HTTP {status}: {text}")
    for text, n in Counter(m.strip().splitlines()[-1][:160] for m in tap.messages if m.strip()).most_common(8):
        print(f"  {n:4d} x server: {text}")
    reasons = must_fail(recs, tap.messages)
    print("\nFAIL: " + "; ".join(reasons) if reasons else "\nPASS")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
