"""A seeded demo day for visitors without garments (docs/backend_spec.md, section 8). Every row is flagged demo.

One men's-shirt session: a line plan of 8 operations on numbered machines, 6 bundles, 42 completed garments, and
checker-confirmed defects drawn with a fixed seed, including a skipped-stitch run on machine 7's collar so a trend
alert shows. Demo inspections carry no photo until the Kadiri gallery replaces them.
"""
import random

from stitchcheck.backend import db
from stitchcheck.qc import counts

LINE_PLAN = [("collar", "OP01", "7"), ("placket", "OP02", "12"), ("cuff", "OP03", "3"), ("sleeve", "OP04", "15"),
             ("side_seam", "OP05", "18"), ("pocket", "OP06", "9"), ("hem", "OP07", "21"), ("shoulder", "OP08", "5")]
SEVERITY = {"skipped_stitch": "major", "broken_stitch": "major", "stain_damage": "major", "crooked_seam": "minor",
            "puckering": "minor", "open_seam": "major"}


def seed_demo(con, seed=7, date="2026-10-28"):
    """Create the demo session if none exists; returns its id."""
    row = con.execute("SELECT id FROM session WHERE is_demo = 1 ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    rng = random.Random(seed)
    sid = db.insert(con, "session", date=date, style="Men's shirt (demo)", fabric="A", lot_size=500, is_demo=1)
    for i, (part, op, mc) in enumerate(LINE_PLAN, 1):
        db.insert(con, "line_plan", session_id=sid, part=part, sequence_no=i, operator_id=op, machine_id=mc)
    plan = [dict(part=p, operator_id=o, machine_id=m) for p, o, m in LINE_PLAN]
    minute = 0
    for bundle_no in range(1, 7):
        bid = db.insert(con, "bundle", session_id=sid, bundle_no=bundle_no)
        for n in range(1, 8):
            spot = counts.spot_check_part(plan, ["collar", "placket"], f"demo:{bundle_no}:{n}")
            gid = db.insert(con, "garment", bundle_id=bid, garment_no=n, spot_check_part=spot)
            for part in ["collar", "placket"] + ([spot] if spot else []):
                op, mc = counts.map_part(plan, part)
                label = "good"
                if part == "collar" and bundle_no == 5 and n in (2, 4, 6):
                    label = "skipped_stitch"  # the run the trend alert catches
                elif rng.random() < 0.08:
                    label = rng.choice(["broken_stitch", "crooked_seam", "stain_damage", "skipped_stitch", "puckering"])
                decision = "not_a_defect" if label == "good" else rng.choice(["agree", "agree", "agree", "changed"])
                db.insert(con, "inspection", garment_id=gid, reason="critical" if part != spot else "spot_check",
                          photo_path="demo", seam_found=1, review_status="done", proposed_label=label,
                          proposed_severity=SEVERITY.get(label, "none"), needs_checker=int(rng.random() < 0.2),
                          final_label=label, final_part=part, final_severity=SEVERITY.get(label, "none"),
                          operator_id=op, machine_id=mc, decision=decision, decided_via="tap", config_hash="demo")
            minute += 3
            db.update(con, "garment", gid, status="complete", completed_at=f"{date}T{9 + minute // 60:02d}:{minute % 60:02d}:00")
            _alerts(con, sid)  # as the app does when a garment is completed
    return sid


def _alerts(con, sid):
    for machine, defect, garments in counts.trend_alerts(db.session_inspections(con, sid), db.completed_garments(con, sid)):
        seen = con.execute("SELECT garment_ids FROM alert WHERE session_id = ? AND machine_id = ? AND defect = ?",
                           (sid, machine, defect)).fetchall()
        if not any(set(garments) <= set(a["garment_ids"]) for a in seen):
            db.insert(con, "alert", session_id=sid, machine_id=machine, defect=defect, garment_ids=garments)
