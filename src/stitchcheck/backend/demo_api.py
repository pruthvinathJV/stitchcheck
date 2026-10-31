"""Demo mode for visitors without garments (one extension: register(app, ctx)).

- POST /demo/session: the visitor's own deep copy of the seeded demo day (owner = the signed sc_visitor cookie),
  plus one empty bundle for their own garments. A visitor with a live copy gets it back.
- POST /demo/reset: throws the copy away (rows and photos) and makes a fresh one.
- GET /demo/gallery and GET /gallery-media/{file}: sample seam photos (data/gallery/manifest.json, built by
  scripts/ops/build_gallery.py) for visitors without a garment at hand.
- POST /demo/gallery/{id}: runs a gallery photo through the same step 1 and background review as a phone photo,
  into a garment of the visitor's copy. SAM answers for gallery photos are cached on disk, so repeat clicks cost
  nothing (the Muse Spark review is already cached by inspect()).
Copies older than 24 hours are deleted on start and then at most once an hour, checked on demo requests.
"""
import contextvars
import hashlib
import io
import json
import os
import secrets
import shutil
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from stitchcheck.backend import db
from stitchcheck.backend.access import (PAUSED, VISITOR_COOKIE, client_ip, is_https, make_visitor, safe_mode,
                                        visitor_id)
from stitchcheck.backend.app import PARTS
from stitchcheck.backend.demo import seed_demo, seeded_session
from stitchcheck.backend.pipeline import SEAM_PROMPT

COPY_TTL_HOURS = 24
CLEANUP_EVERY_S = 3600
COPIES_PER_IP_PER_HOUR = 20  # new copies (session or reset) per visitor IP
COOKIE_MAX_AGE = 86400
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
GALLERY_TAG = "sc_gallery_sha"  # set on a gallery image's info so the background part check also uses the cache
_gallery_sha = contextvars.ContextVar("sc_gallery_sha", default=None)


def gallery_dir():
    return Path(os.environ.get("STITCHCHECK_GALLERY_DIR", "data/gallery"))


def gallery_items():
    """Manifest entries with an id and an image file name; [] when the folder or manifest is missing."""
    try:
        items = json.loads((gallery_dir() / "manifest.json").read_text())
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("image"), str)]


def gallery_file(name):
    """A file directly inside the gallery folder with an image suffix, or None."""
    root = gallery_dir().resolve()
    p = (root / name).resolve()
    return p if p.parent == root and p.suffix.lower() in IMAGE_SUFFIXES and p.is_file() else None


# ---- SAM cache for gallery photos ------------------------------------------------------------------------------

class CachedSam:
    """Wraps deps.sam. Calls for a gallery photo (marked by _gallery_sha or the image's info) are answered from a
    disk cache keyed by sha256(image bytes) and the prompt; other calls pass straight through. In safe mode no
    live SAM call is made: other photos raise, uncached gallery prompts answer "nothing found"."""

    def __init__(self, inner, cache_dir):
        self.inner, self.cache_dir = inner, Path(cache_dir)

    def path(self, sha, prompt):
        return self.cache_dir / f"{sha}_{hashlib.sha256(prompt.encode()).hexdigest()[:16]}.npz"

    def cached(self, sha, prompt):
        return self.path(sha, prompt).exists()

    def __call__(self, im, prompt):
        info = getattr(im, "info", None)
        sha = _gallery_sha.get() or (info or {}).get(GALLERY_TAG)
        if sha is None:
            if safe_mode():
                raise RuntimeError(PAUSED)
            return self.inner(im, prompt)
        if isinstance(info, dict):
            info[GALLERY_TAG] = sha
        p = self.path(sha, prompt)
        if p.exists():
            try:
                return _load(p)
            except Exception:
                p.unlink(missing_ok=True)  # a damaged entry: ask again
        if safe_mode():
            return [], ""
        out = self.inner(im, prompt)
        _save(p, out)
        return out


def _plain(x):
    return x.item() if hasattr(x, "item") else list(x)


def _save(path, out):
    objs, raw = out
    meta, arrays = [], {}
    for i, o in enumerate(objs):
        m = {k: v for k, v in o.items() if k != "mask"}
        if o.get("mask") is not None:
            arrays[f"mask{i}"] = np.asarray(o["mask"])
            m["_mask"] = f"mask{i}"
        meta.append(m)
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.array(json.dumps({"objects": meta, "raw": raw or ""}, default=_plain)), **arrays)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(buf.getvalue())
    os.replace(tmp, path)


def _load(path):
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
        objs = []
        for m in meta["objects"]:
            key = m.pop("_mask", None)
            if key:
                m["mask"] = z[key]
            if isinstance(m.get("box"), list):
                m["box"] = tuple(m["box"])
            objs.append(m)
    return objs, meta["raw"]


def _inspect_cached(ctx, s1, sess):
    """Whether the review of this crop is already in inspect()'s cache for the brain that would answer. True when
    it cannot be known (the brain clients refuse live calls in safe mode anyway)."""
    try:
        from stitchcheck.spark import inspect as hybrid
        from stitchcheck.spark.classify import _url
        order = ctx.brains.order(sess["brain"])
        if not order:
            return True
        b = order[0]
        url, ref_url = _url(s1["crop_path"], 448), _url(ctx.deps.reference(sess["fabric"]), 448)
        tool_out = json.dumps({"top2": [[lab, round(float(p), 3)] for lab, p in s1["classifier_top2"]]})
        key = hashlib.sha256(json.dumps(["inspect", b.model, hybrid.INSPECT_PROMPT, b.effort, url, ref_url, tool_out])
                             .encode()).hexdigest()[:32]
        return (Path(hybrid.CACHE_DIR) / f"{key}.json").exists()
    except Exception:
        return True


# ---- copies of the demo day ------------------------------------------------------------------------------------

def _ins(con, table, row):
    """Insert without committing (the copy is one transaction)."""
    cols = list(row)
    vals = [json.dumps(v) if isinstance(v, (dict, list, tuple)) else v for v in row.values()]
    return con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals).lastrowid


def _rows(con, sql, *args):
    return con.execute(sql, args).fetchall()


def copy_session(con, src, owner):
    """Deep copy of session src (line plan, bundles, garments, inspections, voice notes, alerts, reports, audits)
    with new ids, owned by `owner`, plus one empty bundle numbered after the last. Returns (session_id, bundle_id).
    The caller holds the write lock."""
    try:
        s = _rows(con, "SELECT * FROM session WHERE id = ?", src)[0]
        sid = _ins(con, "session", {k: v for k, v in s.items() if k not in ("id", "created_at")} | {"is_demo": 1, "owner": owner})
        for r in _rows(con, "SELECT * FROM line_plan WHERE session_id = ?", src):
            _ins(con, "line_plan", r | {"session_id": sid})
        bmap, gmap, imap = {}, {}, {}
        for r in _rows(con, "SELECT * FROM bundle WHERE session_id = ? ORDER BY id", src):
            bmap[r["id"]] = _ins(con, "bundle", _drop(r) | {"session_id": sid})
        for r in _rows(con, "SELECT g.* FROM garment g JOIN bundle b ON b.id = g.bundle_id WHERE b.session_id = ? "
                            "ORDER BY g.id", src):
            gmap[r["id"]] = _ins(con, "garment", _drop(r) | {"bundle_id": bmap[r["bundle_id"]]})
        for r in _rows(con, "SELECT i.* FROM inspection i JOIN garment g ON g.id = i.garment_id JOIN bundle b ON "
                            "b.id = g.bundle_id WHERE b.session_id = ? ORDER BY i.id", src):
            imap[r["id"]] = _ins(con, "inspection", _drop(r) | {"garment_id": gmap[r["garment_id"]]})
        if imap:
            marks = ", ".join("?" * len(imap))
            for r in _rows(con, f"SELECT * FROM voice_note WHERE inspection_id IN ({marks}) ORDER BY id", *imap):
                _ins(con, "voice_note", _drop(r) | {"inspection_id": imap[r["inspection_id"]]})
        for r in _rows(con, "SELECT * FROM alert WHERE session_id = ? ORDER BY id", src):
            _ins(con, "alert", _drop(r) | {"session_id": sid, "garment_ids": [gmap.get(g, g) for g in r["garment_ids"] or []]})
        for table in ("report", "audit"):
            for r in _rows(con, f"SELECT * FROM {table} WHERE session_id = ? ORDER BY id", src):
                _ins(con, table, _drop(r) | {"session_id": sid})
        last = max([r["bundle_no"] for r in _rows(con, "SELECT bundle_no FROM bundle WHERE session_id = ?", sid)] or [0])
        bid = _ins(con, "bundle", {"session_id": sid, "bundle_no": last + 1})
        con.commit()
        return sid, bid
    except Exception:
        con.rollback()
        raise


def _drop(r):
    return {k: v for k, v in r.items() if k != "id"}


def _root_mounts_last(app):
    """Keep a catch-all mount at "/" (the static frontend) behind the routes added here."""
    routes = app.router.routes
    root = [r for r in routes if getattr(r, "path", None) == "" and r.__class__.__name__ == "Mount"]
    for r in root:
        routes.remove(r)
        routes.append(r)


class GalleryShot(BaseModel):
    garment_id: int
    part: str


def register(app, ctx):
    con, lock, storage = ctx.con, ctx.lock, ctx.storage
    last_cleanup = [0.0]
    new_copies = defaultdict(deque)  # ip -> times a copy was made
    if not isinstance(ctx.deps.sam, CachedSam):
        ctx.deps.sam = CachedSam(ctx.deps.sam, os.environ.get("STITCHCHECK_SAM_CACHE") or storage.parent / "sam_cache")
    sam = ctx.deps.sam

    def delete_copies(sids):
        """The caller holds the lock. Rows go by cascade; agent runs stay for the spend figures."""
        for sid in sids:
            con.execute("DELETE FROM session WHERE id = ? AND is_demo = 1 AND owner IS NOT NULL", (sid,))
        con.commit()
        for sid in sids:
            shutil.rmtree(storage / str(int(sid)), ignore_errors=True)

    def cleanup(force=False):
        now = time.time()
        if not force and now - last_cleanup[0] < CLEANUP_EVERY_S:
            return
        last_cleanup[0] = now
        with lock:
            old = [r["id"] for r in con.execute(
                "SELECT id FROM session WHERE is_demo = 1 AND owner IS NOT NULL AND created_at < datetime('now', ?)",
                (f"-{COPY_TTL_HOURS} hours",)).fetchall()]
            delete_copies(old)

    def live_copy(vid):
        r = con.execute("SELECT id FROM session WHERE is_demo = 1 AND owner = ? AND created_at >= datetime('now', ?) "
                        "ORDER BY id DESC LIMIT 1", (vid, f"-{COPY_TTL_HOURS} hours")).fetchone()
        return r["id"] if r else None

    def visitor_bundle(sid):
        """The copy's own bundle: the first numbered after the seeded day's bundles (made if missing)."""
        src = seeded_session(con)
        n = con.execute("SELECT MAX(bundle_no) AS n FROM bundle WHERE session_id = ?", (src,)).fetchone()["n"] or 0
        r = con.execute("SELECT id FROM bundle WHERE session_id = ? AND bundle_no > ? ORDER BY bundle_no LIMIT 1",
                        (sid, n)).fetchone()
        return r["id"] if r else db.insert(con, "bundle", session_id=sid, bundle_no=n + 1)

    def allow_new_copy(request):
        q, now = new_copies[client_ip(request)], time.time()
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= COPIES_PER_IP_PER_HOUR:
            raise HTTPException(429, "too many demo copies from this address; try again later")
        q.append(now)

    def respond(request, sid, bid, cookie):
        resp = JSONResponse({"session_id": sid, "bundle_id": bid})
        if cookie:
            resp.set_cookie(VISITOR_COOKIE, cookie, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                            secure=is_https(request))
        return resp

    def copy_for(request, reset):
        cleanup()
        vid, cookie = visitor_id(request), None
        if vid is None:
            vid, cookie = make_visitor(app)
        with lock:
            old = [r["id"] for r in con.execute("SELECT id FROM session WHERE is_demo = 1 AND owner = ?", (vid,)).fetchall()]
            sid = None if reset else live_copy(vid)
            if sid is None:
                allow_new_copy(request)
                src = seeded_session(con) or seed_demo(con)
                sid, bid = copy_session(con, src, vid)
                if reset:  # after the copy, so the fresh copy never reuses the old ids
                    delete_copies(old)
            else:
                bid = visitor_bundle(sid)
        return respond(request, sid, bid, cookie)

    @app.post("/demo/session")
    def demo_session(request: Request):
        return copy_for(request, reset=False)

    @app.post("/demo/reset")
    def demo_reset(request: Request):
        return copy_for(request, reset=True)

    @app.get("/demo/gallery")
    def gallery():
        cleanup()
        return [{"id": i["id"], "title_te": i.get("title_te", ""), "title_en": i.get("title_en", ""),
                 "part": i.get("part", ""), "thumb_url": f"/gallery-media/{i['image']}"} for i in gallery_items()]

    @app.get("/gallery-media/{name}")
    def gallery_media(name: str):
        p = gallery_file(name)
        if p is None:
            raise HTTPException(404, "not found")
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/demo/gallery/{item_id}")
    def gallery_shot(item_id: str, shot: GalleryShot, request: Request, background: BackgroundTasks):
        cleanup()
        item = next((i for i in gallery_items() if i["id"] == item_id), None)
        vid = visitor_id(request)
        if item is None or vid is None:
            raise HTTPException(404, "not found")
        sess = ctx.session_of_garment(shot.garment_id)
        s = con.execute("SELECT is_demo, owner FROM session WHERE id = ?", (sess["session_id"],)).fetchone()
        if not (s and s["is_demo"] and s["owner"] == vid):
            raise HTTPException(404, "not found")
        if shot.part not in PARTS:
            raise HTTPException(422, f"unknown part {shot.part}")
        path = gallery_file(item["image"])
        if path is None:
            raise HTTPException(404, "not found")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if safe_mode() and not sam.cached(sha, SEAM_PROMPT):
            raise HTTPException(503, PAUSED)
        token = _gallery_sha.set(sha)
        try:
            s1, im = ctx.step1(data, sess["session_id"])
        finally:
            _gallery_sha.reset(token)
        im.info[GALLERY_TAG] = sha
        if not s1["seam_found"]:
            return {"retake": s1["retake"], "step1_seconds": s1["step1_seconds"]}
        if safe_mode() and not _inspect_cached(ctx, s1, sess):
            raise HTTPException(503, PAUSED)
        iid = ctx.write(db.insert, "inspection", garment_id=shot.garment_id, reason="suspicious",
                        photo_path=s1["photo_path"], crop_path=s1["crop_path"], overlay_path=s1["overlay_path"],
                        seam_found=1, scale_path=s1["scale_path"], classifier_top2=s1["classifier_top2"],
                        final_part=None, config_hash=s1["config_hash"],
                        timings={"step1": s1["step1_seconds"], "tile_side": s1["tile_side"],
                                 "stitch_pitch": s1["stitch_pitch"], "gallery": item_id})
        background.add_task(ctx.review, iid, s1, im, shot.part, sess)
        return {"inspection_id": iid, "review_status": "reviewing", "crop_url": ctx.url(s1["crop_path"]),
                "overlay_url": ctx.url(s1["overlay_path"]), "classifier_label": s1["classifier_top2"][0][0],
                "classifier_top2": s1["classifier_top2"], "scale_path": s1["scale_path"],
                "step1_seconds": s1["step1_seconds"]}

    cleanup(force=True)
    _root_mounts_last(app)
