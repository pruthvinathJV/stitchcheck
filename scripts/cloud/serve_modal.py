"""Host the StitchCheck API and the phone app on Modal: modal deploy scripts/cloud/serve_modal.py (from the repo root)

One container at most (SQLite has a single writer). The ONNX ensemble lives in the "stitchcheck-models" volume (upload
once: modal volume put stitchcheck-models models/demo_ensemble.onnx demo_ensemble.onnx); the database and photos live
in "stitchcheck-app"; the demo gallery photos in "stitchcheck-gallery"; nightly database copies in
"stitchcheck-backups". MODEL_API_KEY comes from the Modal secret "stitchcheck-meta" (MODEL_API_KEY, plus
MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET for the Muse Spark Open fallback). The optional secret
"stitchcheck-app-config" carries STITCHCHECK_SAFE_MODE, STITCHCHECK_UNIT_PIN, STITCHCHECK_ADMIN_TOKEN and
STITCHCHECK_SECRET; when it does not exist the deploy prints a warning and goes ahead without it. The URL is public;
spend is bounded by the per-IP limit and the daily caps per brain (backend/brains.py). Runbook: docs/ops.md.

Deploy-time settings (read on the machine that runs modal deploy):
  STITCHCHECK_WARM=1 modal deploy scripts/cloud/serve_modal.py   keep one container warm (judging, Nov 5 to 8);
                                                                 the default 0 scales to zero when idle
  STITCHCHECK_ALLOW_DIRTY=1                                       deploy even with uncommitted changes outside app/
The deploying commit (git rev-parse --short HEAD) is baked into the image as STITCHCHECK_COMMIT and served by /version.

Scheduled functions in the same app: uptime (every 10 minutes: /health and /version), synthetic (hourly at :05: the
no-AI part of the smoke flow), both appending JSON lines to /vol/uptime.jsonl; backup (daily at 03:00 UTC: a sqlite3
backup of /vol/stitchcheck.db to /backups/stitchcheck-YYYYMMDD.db, newest 7 kept, logged to /backups/backup.jsonl).
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import modal

REPO = Path(__file__).resolve().parent.parent.parent  # the repo root locally; "/" in a container
DEFAULT_URL = "https://jvpnath--stitchcheck-web-web.us-east.modal.run"
KEEP_BACKUPS = 7
OPTIONAL_SECRET = "stitchcheck-app-config"  # STITCHCHECK_SAFE_MODE, _UNIT_PIN, _ADMIN_TOKEN, _SECRET
LOCAL = modal.is_local()
CLI = LOCAL and sys.argv[1:2] in (["deploy"], ["serve"], ["run"], ["shell"])  # loaded by the modal command line


def dirty_outside_app(porcelain):
    """Paths from `git status --porcelain` output that lie outside app/ (frontend edits may ship uncommitted)."""
    paths = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if not (path == "app" or path.startswith("app/")):
            paths.append(path)
    return paths


def backup_name(when):
    return f"stitchcheck-{when:%Y%m%d}.db"


def backups_to_prune(names, keep=KEEP_BACKUPS):
    """Backup file names to delete so that only the newest `keep` remain (names sort by date)."""
    dated = sorted(n for n in names if n.startswith("stitchcheck-") and n.endswith(".db"))
    return dated[:-keep] if keep > 0 else dated


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=20, check=True).stdout.strip()


def deploy_commit():
    try:
        return git("rev-parse", "--short", "HEAD") or "unknown"
    except Exception:
        return "unknown"


def refuse_dirty_deploy():
    if os.environ.get("STITCHCHECK_ALLOW_DIRTY") == "1":
        return
    try:
        dirty = dirty_outside_app(git("status", "--porcelain"))
    except Exception:
        return  # not a git checkout: nothing to compare against, the commit reads "unknown"
    if dirty:
        raise SystemExit("Refusing to deploy: uncommitted changes outside app/ would not match STITCHCHECK_COMMIT:\n  "
                         + "\n  ".join(dirty[:20]) + "\nCommit them, or set STITCHCHECK_ALLOW_DIRTY=1 to deploy anyway.")


def optional_secret(name):
    """The named secret if it exists; None (with a warning) if not, so a missing config never blocks a deploy."""
    if not CLI:
        return None  # tests and imports: no network; in the container the secrets are already in the environment
    secret = modal.Secret.from_name(name)
    try:
        secret.hydrate()
    except modal.exception.NotFoundError:
        print(f"warning: Modal secret {name!r} not found; deploying without it (safe mode, unit PIN and admin token "
              "off). Create it with: modal secret create " + name + " STITCHCHECK_SAFE_MODE=0 ...", file=sys.stderr)
        return None
    return secret


if sys.argv[1:2] == ["deploy"] and LOCAL:
    refuse_dirty_deploy()
COMMIT = deploy_commit() if LOCAL else os.environ.get("STITCHCHECK_COMMIT", "unknown")
WARM = int(os.environ.get("STITCHCHECK_WARM", "0") or 0)

if LOCAL:
    from stitchcheck.data.stitchingnet import SEG_DIR
    LOCKED_REF = SEG_DIR / "F. Satin-Core/0. Normal/image/F_00_131.jpg"  # results/test/lock.json (CC BY 4.0)
else:  # the container already has the file baked in; only the image definition refers to it
    LOCKED_REF = Path("data/raw/stitchingnet_seg/extracted/dataset/F. Satin-Core/0. Normal/image/F_00_131.jpg")

image = (modal.Image.debian_slim(python_version="3.12")
         .apt_install("ffmpeg")
         .pip_install("fastapi==0.162.0", "python-multipart==0.0.32", "onnxruntime==1.31.1", "numpy==2.5.4",
                      "pillow==13.0.0", "pandas==3.1.0", "openai==3.39.0", "requests==2.34.2", "meta-sam-parser==0.1.0",
                      "python-dotenv==1.2.4")
         .env({"STITCHCHECK_DB": "/vol/stitchcheck.db", "STITCHCHECK_STORAGE": "/vol/photos",
               "STITCHCHECK_ONNX": "/models/demo_ensemble.onnx", "STITCHCHECK_REFS": "/vol/ref_tiles",
               "STITCHCHECK_FALLBACK_REF": "/app/assets/reference_good.jpg", "STITCHCHECK_APP_DIR": "/app/app",
               "STITCHCHECK_GALLERY_DIR": "/gallery", "STITCHCHECK_COMMIT": COMMIT})
         .add_local_file("results/test/lock.json", "/app/results/test/lock.json")
         .add_local_file(str(LOCKED_REF), "/app/assets/reference_good.jpg")
         .add_local_dir("app", "/app/app")
         .add_local_python_source("stitchcheck"))
ops_image = modal.Image.debian_slim(python_version="3.12").pip_install("requests==2.34.2")

app = modal.App("stitchcheck-web")
models = modal.Volume.from_name("stitchcheck-models", create_if_missing=True)
appdata = modal.Volume.from_name("stitchcheck-app", create_if_missing=True)
gallery = modal.Volume.from_name("stitchcheck-gallery", create_if_missing=True)
backups = modal.Volume.from_name("stitchcheck-backups", create_if_missing=True)
secrets = [modal.Secret.from_name("stitchcheck-meta")] + [s for s in [optional_secret(OPTIONAL_SECRET)] if s]


@app.function(image=image, secrets=secrets, volumes={"/models": models, "/vol": appdata, "/gallery": gallery},
              cpu=1.0, memory=1536, min_containers=WARM, max_containers=1, scaledown_window=300, timeout=600)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    os.chdir("/app")  # relative paths (lock file, Spark cache) resolve here
    from stitchcheck.backend.app import default_app
    api = default_app()

    import threading
    import time

    def commit_loop():  # persist the database and photos beyond this container, including background reviews
        while True:
            time.sleep(10)
            try:
                appdata.commit()
            except Exception as ex:
                print("volume commit failed:", ex, flush=True)

    threading.Thread(target=commit_loop, daemon=True).start()

    @api.get("/health/memory")
    def memory():
        import resource
        return {"max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}

    return api


# Monitoring and backups --------------------------------------------------------------------------------------------

def _base_url():
    url = os.environ.get("STITCHCHECK_PUBLIC_URL")
    if url:
        return url.rstrip("/")
    try:
        return web.get_web_url().rstrip("/")
    except Exception:
        return DEFAULT_URL


def _get(http, method, url, **kw):
    """(status or None, ms, parsed JSON or text or error message). Never raises."""
    import time
    t = time.perf_counter()
    try:
        r = http.request(method, url, timeout=30, **kw)
        ms = round((time.perf_counter() - t) * 1000)
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        return r.status_code, ms, body
    except Exception as ex:
        return None, round((time.perf_counter() - t) * 1000), f"{type(ex).__name__}: {ex}"[:200]


def _version_of(status, body):
    if status == 404:
        return "not deployed yet"
    if status == 200 and isinstance(body, dict):
        return body.get("commit") or body
    return body if status == 200 else f"error {status}: {body}"


def _log(record, path="/vol/uptime.jsonl"):
    """Append one JSON line to the app volume and commit it. Never raises."""
    import json
    print(json.dumps(record), flush=True)
    try:
        appdata.reload()
    except Exception as ex:
        print("volume reload failed:", ex, flush=True)
    try:
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
        appdata.commit()
    except Exception as ex:
        print("uptime log not written:", ex, flush=True)


MONITOR_FROM, MONITOR_TO = "2026-11-04", "2026-11-09"  # judging window; outside it the monitors do nothing


def _monitoring_now():
    """Pings wake the scaled-down container, so they only run while judges may be using the app."""
    return MONITOR_FROM <= datetime.now(timezone.utc).date().isoformat() <= MONITOR_TO


@app.function(image=ops_image, volumes={"/vol": appdata}, schedule=modal.Cron("*/10 * * * *"), timeout=120)
def uptime():
    """GET /health and /version; one line per run in /vol/uptime.jsonl."""
    if not _monitoring_now():
        return
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "uptime"}
    try:
        import requests
        http = requests.Session()
        base = _base_url()
        status, ms, body = _get(http, "GET", base + "/health")
        vstatus, _, vbody = _get(http, "GET", base + "/version")
        record |= {"health_ms": ms, "ok": status == 200 and isinstance(body, dict) and body.get("ok") is True,
                   "status": status, "version": _version_of(vstatus, vbody)}
        if not record["ok"]:
            record["error"] = body if isinstance(body, str) else None
    except Exception as ex:
        record |= {"ok": False, "status": None, "error": f"{type(ex).__name__}: {ex}"[:200]}
    _log(record)


@app.function(image=ops_image, volumes={"/vol": appdata}, schedule=modal.Cron("5 * * * *"), timeout=300)
def synthetic():
    """The no-AI part of scripts/ops/smoke_e2e.py: demo session, its dashboard, the gallery. Offset from uptime by 5
    minutes so the two never append to the log at the same time."""
    if not _monitoring_now():
        return
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "synthetic"}
    try:
        import requests
        http = requests.Session()  # keeps the visitor cookie between calls
        base = _base_url()
        steps = {}
        status, ms, body = _get(http, "GET", base + "/health")
        steps["health"] = {"status": status, "ms": ms}
        vstatus, _, vbody = _get(http, "GET", base + "/version")
        s, ms2, demo = _get(http, "POST", base + "/demo/session")
        steps["demo_session"] = {"status": s, "ms": ms2}
        sid = demo.get("session_id") if s == 200 and isinstance(demo, dict) else None
        if s == 404:
            steps["demo_session"]["note"] = "not deployed yet; reading the seeded session 1"
            sid = 1
        if sid is not None:
            d, ms3, dash = _get(http, "GET", f"{base}/sessions/{sid}/dashboard")
            steps["dashboard"] = {"status": d, "ms": ms3}
        g, ms4, _ = _get(http, "GET", base + "/demo/gallery")
        steps["gallery"] = {"status": g, "ms": ms4} | ({"note": "not deployed yet"} if g == 404 else {})
        required = [steps["health"], steps.get("dashboard", {"status": None})]
        if s != 404:
            required.append(steps["demo_session"])
        if g != 404:
            required.append(steps["gallery"])
        record |= {"health_ms": ms, "ok": all(x["status"] == 200 for x in required), "status": status,
                   "version": _version_of(vstatus, vbody), "steps": steps}
    except Exception as ex:
        record |= {"ok": False, "status": None, "error": f"{type(ex).__name__}: {ex}"[:200]}
    _log(record)


@app.function(image=ops_image, volumes={"/vol": appdata, "/backups": backups}, schedule=modal.Cron("0 3 * * *"),
              timeout=600)
def backup():
    """Copy the live database with sqlite3's backup API, keep the newest KEEP_BACKUPS copies, commit the volume."""
    import json
    import shutil
    import sqlite3
    import tempfile
    from contextlib import closing

    now = datetime.now(timezone.utc)
    record = {"ts": now.isoformat(timespec="seconds"), "kind": "backup", "ok": False}
    try:
        appdata.reload()
        src = Path("/vol/stitchcheck.db")
        dest = Path("/backups") / backup_name(now)
        with tempfile.TemporaryDirectory() as tmp:
            # Work from a private copy: a hot journal captured mid-write is rolled back here, not on the live volume
            local = Path(tmp) / src.name
            for suffix in ("", "-journal", "-wal", "-shm"):
                if Path(str(src) + suffix).exists():
                    shutil.copy2(str(src) + suffix, str(local) + suffix)
            part = Path(tmp) / "backup.db"
            with closing(sqlite3.connect(local)) as s, closing(sqlite3.connect(part)) as d:
                s.backup(d)
                check = d.execute("PRAGMA integrity_check").fetchone()[0]
                sessions = d.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"integrity_check: {check}")
            shutil.copy2(part, dest)
        deleted = backups_to_prune([p.name for p in Path("/backups").iterdir()])
        for name in deleted:
            (Path("/backups") / name).unlink()
        record |= {"ok": True, "file": dest.name, "bytes": dest.stat().st_size, "sessions": sessions, "deleted": deleted}
    except Exception as ex:
        record["error"] = f"{type(ex).__name__}: {ex}"[:300]
    print(json.dumps(record), flush=True)
    try:
        with open("/backups/backup.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
        backups.commit()
    except Exception as ex:
        print("backup volume commit failed:", ex, flush=True)
