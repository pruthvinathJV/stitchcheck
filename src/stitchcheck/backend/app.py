"""The StitchCheck API (docs/backend_spec.md). Run: uvicorn stitchcheck.backend.app:default_app --factory

Every dependency (SAM, classifier ensemble, Spark client, transcription, reference images) is injected through
create_app(), so tests run with fakes and the server runs with the real ones.
"""
import hashlib
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from stitchcheck.backend import db
from stitchcheck.backend.pipeline import clean_photo, part_chip, step1, step2
from stitchcheck.backend.voice_parse import parse_correction
from stitchcheck.classifier.train import CLASSES
from stitchcheck.qc import counts
from stitchcheck.spark import inspect as hybrid

PRICE_IN, PRICE_OUT = 1.25 / 1e6, 4.25 / 1e6  # muse-spark-1.3 standard, USD per token
PARTS = ("collar", "pocket", "hem", "side_seam", "sleeve", "placket", "cuff", "shoulder")


@dataclass
class Deps:
    db_path: str
    storage: str
    sam: Callable
    ensemble: Callable
    spark: object
    transcribe: Callable
    reference: Callable  # fabric -> path of a good-seam reference crop
    classes: list = field(default_factory=lambda: list(CLASSES["clean"]))


class LinePlanRow(BaseModel):
    part: str
    operator_id: str
    machine_id: str


class NewSession(BaseModel):
    date: str
    style: str
    fabric: str
    lot_size: int
    line_plan: list[LinePlanRow]
    critical_parts: list[str] = ["collar", "placket"]
    spot_check_rate: float = 0.1
    is_demo: bool = False


class NewBundle(BaseModel):
    bundle_no: int


class NewGarment(BaseModel):
    garment_no: int


class Decision(BaseModel):
    action: str  # agree | change | not_a_defect | retake
    label: str | None = None
    part: str | None = None
    severity: str | None = None
    operator_id: str | None = None
    machine_id: str | None = None
    via: str = "tap"


def cost(tokens_in, tokens_out):
    return round((tokens_in or 0) * PRICE_IN + (tokens_out or 0) * PRICE_OUT, 6)


def create_app(deps: Deps):
    app = FastAPI(title="StitchCheck")
    con = db.connect(deps.db_path)
    lock = threading.Lock()  # one writer
    storage = Path(deps.storage)

    def write(fn, *a, **k):
        with lock:
            return fn(con, *a, **k)

    def one(sql, *args):
        row = con.execute(sql, args).fetchone()
        if row is None:
            raise HTTPException(404, "not found")
        return row

    def line_plan(session_id):
        return con.execute("SELECT part, operator_id, machine_id, sequence_no FROM line_plan WHERE session_id = ? "
                           "ORDER BY sequence_no", (session_id,)).fetchall()

    def session_of_garment(garment_id):
        return one("SELECT b.session_id, s.fabric FROM garment g JOIN bundle b ON b.id = g.bundle_id "
                   "JOIN session s ON s.id = b.session_id WHERE g.id = ?", garment_id)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/sessions")
    def new_session(s: NewSession):
        bad = [r.part for r in s.line_plan if r.part not in PARTS]
        if bad:
            raise HTTPException(422, f"unknown parts: {bad}")
        sid = write(db.insert, "session", date=s.date, style=s.style, fabric=s.fabric, lot_size=s.lot_size,
                    critical_parts=s.critical_parts, spot_check_rate=s.spot_check_rate, is_demo=int(s.is_demo))
        for i, r in enumerate(s.line_plan, 1):
            write(db.insert, "line_plan", session_id=sid, part=r.part, sequence_no=i, operator_id=r.operator_id,
                  machine_id=r.machine_id)
        return get_session(sid)

    @app.get("/sessions/{sid}")
    def get_session(sid: int):
        return one("SELECT * FROM session WHERE id = ?", sid) | {"line_plan": line_plan(sid)}

    @app.post("/sessions/{sid}/bundles")
    def new_bundle(sid: int, b: NewBundle):
        one("SELECT id FROM session WHERE id = ?", sid)
        return {"id": write(db.insert, "bundle", session_id=sid, bundle_no=b.bundle_no), "bundle_no": b.bundle_no}

    @app.post("/bundles/{bid}/garments")
    def new_garment(bid: int, g: NewGarment):
        s = one("SELECT s.* FROM bundle b JOIN session s ON s.id = b.session_id WHERE b.id = ?", bid)
        plan = line_plan(s["id"])
        spot = counts.spot_check_part(plan, s["critical_parts"], f"{bid}:{g.garment_no}", rate=s["spot_check_rate"])
        gid = write(db.insert, "garment", bundle_id=bid, garment_no=g.garment_no, spot_check_part=spot)
        to_photograph = [p for p in s["critical_parts"] if p in {r["part"] for r in plan}] + ([spot] if spot else [])
        return {"id": gid, "garment_no": g.garment_no, "spot_check_part": spot, "photograph": to_photograph}

    @app.post("/garments/{gid}/complete")
    def complete(gid: int):
        sess = session_of_garment(gid)
        write(db.update, "garment", gid, status="complete", completed_at=datetime.now().isoformat(timespec="seconds"))
        new = []
        insp, done = db.session_inspections(con, sess["session_id"]), db.completed_garments(con, sess["session_id"])
        for machine, defect, garments in counts.trend_alerts(insp, done):
            seen = con.execute("SELECT garment_ids FROM alert WHERE session_id = ? AND machine_id = ? AND defect = ?",
                               (sess["session_id"], machine, defect)).fetchall()
            if any(set(garments) <= set(a["garment_ids"]) for a in seen):
                continue
            aid = write(db.insert, "alert", session_id=sess["session_id"], machine_id=machine, defect=defect,
                        garment_ids=garments)
            new.append({"id": aid, "machine_id": machine, "defect": defect, "garment_ids": garments})
        return {"id": gid, "status": "complete", "new_alerts": new}

    @app.post("/garments/{gid}/photos")
    async def photo(gid: int, background: BackgroundTasks, image: UploadFile = File(...), part: str = Form(...),
                    reason: str = Form("critical")):
        sess = session_of_garment(gid)
        if part not in PARTS:
            raise HTTPException(422, f"unknown part {part}")
        data = await image.read()
        s1, im = step1(data, sam=deps.sam, ensemble=deps.ensemble, out_dir=storage / str(sess["session_id"]),
                       classes=deps.classes)
        if not s1["seam_found"]:
            return {"retake": s1["retake"], "step1_seconds": s1["step1_seconds"]}
        iid = write(db.insert, "inspection", garment_id=gid, reason=reason, photo_path=s1["photo_path"],
                    crop_path=s1["crop_path"], seam_found=1, scale_path=s1["scale_path"],
                    classifier_top2=s1["classifier_top2"], final_part=None, config_hash=s1["config_hash"],
                    timings={"step1": s1["step1_seconds"], "tile_side": s1["tile_side"], "stitch_pitch": s1["stitch_pitch"]})
        background.add_task(review, iid, s1, im, part, sess)
        return {"inspection_id": iid, "review_status": "reviewing", "crop_path": s1["crop_path"],
                "classifier_label": s1["classifier_top2"][0][0], "classifier_top2": s1["classifier_top2"],
                "scale_path": s1["scale_path"], "step1_seconds": s1["step1_seconds"]}

    def review(iid, s1, im, part, sess):
        plan = line_plan(sess["session_id"])
        s2 = step2(s1, client=deps.spark, reference=deps.reference(sess["fabric"]), line_plan=plan, part=part)
        v = s2["spark_verdict"]
        tin, tout = s2["tokens"]
        run_id = write(db.insert, "agent_run", kind="inspect",
                       request={"crop": s1["crop_path"], "reference": str(deps.reference(sess["fabric"])),
                                "prompt_sha": hashlib.sha256(hybrid.INSPECT_PROMPT.encode()).hexdigest()[:16]},
                       tool_calls=[{"name": "classify_stitch", "arguments": {}}] if v.get("tool_called") else [],
                       tool_outputs=[{"top2": s1["classifier_top2"]}] if v.get("tool_called") else [],
                       final={k: v.get(k) for k in ("spark_verdict", "spark_label", "pred", "protected", "spark_error")}
                       | {"reason": s2["reason"], "proposed": s2["proposed_label"]},
                       latency_ms=int(s2["step2_seconds"] * 1000), tokens_in=tin, tokens_out=tout, cost_usd=cost(tin, tout))
        timings = one("SELECT timings FROM inspection WHERE id = ?", iid)["timings"] | {"step2": s2["step2_seconds"]}
        write(db.update, "inspection", iid, spark_verdict=v | {"reason": s2["reason"]}, proposed_label=s2["proposed_label"],
              proposed_severity=s2["proposed_severity"], needs_checker=int(s2["needs_checker"]), final_part=part,
              operator_id=s2["operator_id"], machine_id=s2["machine_id"], agent_run_id=run_id, timings=timings,
              review_status="done")
        try:
            chip = part_chip(im, deps.sam, part)
            write(db.update, "inspection", iid, sam_part=chip["sam_part"], sam_part_conf=chip["sam_part_conf"],
                  part_chip=chip["part_chip"])
        except Exception:
            pass  # the chip is optional

    @app.get("/inspections/{iid}")
    def get_inspection(iid: int):
        return one("SELECT * FROM inspection WHERE id = ?", iid)

    @app.post("/inspections/{iid}/decision")
    def decide(iid: int, d: Decision):
        row = one("SELECT * FROM inspection WHERE id = ?", iid)
        if d.via not in ("tap", "voice"):
            raise HTTPException(422, "via must be tap or voice")
        if d.action == "agree":
            if row["review_status"] != "done":
                raise HTTPException(409, "still reviewing")
            f = dict(final_label=row["proposed_label"], final_severity=row["proposed_severity"], decision="agree")
        elif d.action == "change":
            label = d.label or row["proposed_label"]
            sev = d.severity or ("none" if label == "good" else hybrid.severity(label, None))
            f = dict(final_label=label, final_severity=sev, decision="changed")
            if d.part:
                plan = line_plan(session_of_garment(row["garment_id"])["session_id"])
                op, mc = counts.map_part(plan, d.part)
                f |= dict(final_part=d.part, operator_id=op, machine_id=mc)
            f |= {k: v for k, v in (("operator_id", d.operator_id), ("machine_id", d.machine_id)) if v}
        elif d.action == "not_a_defect":
            f = dict(final_label="good", final_severity="none", decision="not_a_defect")
        elif d.action == "retake":
            f = dict(decision="retake")
        else:
            raise HTTPException(422, "action must be agree, change, not_a_defect or retake")
        write(db.update, "inspection", iid, decided_via=d.via, **f)
        return get_inspection(iid)

    @app.post("/inspections/{iid}/voice")
    async def voice(iid: int, audio: UploadFile = File(...)):
        row = one("SELECT * FROM inspection WHERE id = ?", iid)
        sess = session_of_garment(row["garment_id"])
        path = storage / str(sess["session_id"]) / "voice" / f"{iid}_{int(time.time())}{Path(audio.filename or 'a.m4a').suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(await audio.read())
        transcript = deps.transcribe(path)
        parsed = parse_correction(deps.spark, transcript, line_plan(sess["session_id"]))
        run_id = write(db.insert, "agent_run", kind="voice", request={"transcript": transcript}, tool_calls=[],
                       tool_outputs=[], final={"proposed": parsed["proposed"], "ambiguous": parsed["ambiguous"],
                                               "label_candidates": parsed["label_candidates"]},
                       latency_ms=int(parsed["seconds"] * 1000), tokens_in=parsed["prompt_tokens"],
                       tokens_out=parsed["completion_tokens"], cost_usd=cost(parsed["prompt_tokens"], parsed["completion_tokens"]))
        vid = write(db.insert, "voice_note", inspection_id=iid, audio_path=str(path), transcript=transcript,
                    parsed={"proposed": parsed["proposed"], "ambiguous": parsed["ambiguous"], "agent_run_id": run_id})
        return {"voice_note_id": vid, "transcript": transcript, "proposed": parsed["proposed"],
                "ambiguous": parsed["ambiguous"], "label_candidates": parsed["label_candidates"], "agent_run_id": run_id}

    @app.get("/sessions/{sid}/dashboard")
    def dashboard(sid: int):
        one("SELECT id FROM session WHERE id = ?", sid)
        insp, done = db.session_inspections(con, sid), db.completed_garments(con, sid)
        pending = sum(1 for i in insp if i["decision"] == "pending")
        return {
            "completed_garments": len(done), "inspections": len(insp), "pending_decisions": pending,
            "dhu": round(counts.dhu(insp, done), 2), "defective_pct": round(counts.defective_pct(insp, done), 2),
            "by": {by: counts.dhu_by(insp, done, by) for by in counts.BREAKDOWNS},
            "top_issues": counts.worst_combinations(insp, done, n=3),
            "alerts": con.execute("SELECT * FROM alert WHERE session_id = ? ORDER BY id DESC", (sid,)).fetchall(),
            "ai_vs_checker": {
                "agreed": sum(1 for i in insp if i["decision"] == "agree"),
                "changed": sum(1 for i in insp if i["decision"] == "changed"),
                "not_a_defect": sum(1 for i in insp if i["decision"] == "not_a_defect"),
                "sent_to_checker": sum(1 for i in insp if i["needs_checker"]),
            },
        }

    @app.get("/agent-runs/{rid}")
    def agent_run(rid: int):
        r = one("SELECT * FROM agent_run WHERE id = ?", rid)
        steps = [{"call": c, "output": o} for c, o in zip(r["tool_calls"] or [], r["tool_outputs"] or [])]
        return r | {"steps": steps}

    app.state.con = con
    return app


def default_app():
    """The real dependencies (API keys from .env, the 11-fabric demo ensemble)."""
    from stitchcheck.classifier.ensemble import Ensemble
    from stitchcheck.data.stitchingnet import image_path, load_split
    from stitchcheck.meta_api import sam_segment, spark_client
    from stitchcheck.voice import transcribe_correction

    split = load_split()
    fallback = image_path(split.query("split == 'train' and fabric == 'Satin' and label == 'good'")
                          .sample(1, random_state=0)["path"].item())

    def reference(fabric):
        # The session fabric's good reference: a crop cut by step 1 from its {F}-1-5 reference pair, once the pairs
        # arrive; otherwise the training-fabric fallback used on StitchingNet.
        crop = Path("data/phone/ref_tiles") / f"{fabric}-good.jpg"
        return crop if crop.exists() else fallback

    return create_app(Deps(db_path="data/app/stitchcheck.db", storage="data/app/photos", sam=sam_segment,
                           ensemble=Ensemble(), spark=spark_client(), transcribe=transcribe_correction,
                           reference=reference))
