"""A seeded demo day for visitors without garments (docs/backend_spec.md, section 8). Every row is flagged demo.

One men's-shirt session that looks like a decent new unit: a line plan of 8 operations on numbered machines,
6 bundles, 60 completed garments and a session DHU of about 8. Confirmed defects: a skipped-stitch run on machine 7's
collar, so the trend alert shows, a broken stitch on machine 12's placket and a stain found on a spot check; the rest of the day is what a checker sees on a normal shift:
AI flags the checker overruled, one label the checker changed, and a short queue of flags still waiting for the
checker, spread over several machines (about 15% of inspections go to the checker). Drawn with a fixed seed.

Per-visitor copies (stitchcheck.backend.demo_api) are deep copies of this session; the seeded one itself is
read-only for everyone (stitchcheck.backend.access).
"""
import random

from stitchcheck.backend import db
from stitchcheck.qc import counts

LINE_PLAN = [("collar", "OP01", "7"), ("placket", "OP02", "12"), ("cuff", "OP03", "3"), ("sleeve", "OP04", "15"),
             ("side_seam", "OP05", "18"), ("pocket", "OP06", "9"), ("hem", "OP07", "21"), ("shoulder", "OP08", "5")]
SEVERITY = {"skipped_stitch": "major", "broken_stitch": "major", "stain_damage": "major", "crooked_seam": "minor",
            "puckering": "minor", "open_seam": "major"}
CRITICAL = ["collar", "placket"]
BUNDLES, PER_BUNDLE = 6, 10
DEMO_HASH = "demo-v4"  # marks the current seed; an older seed is replaced on start
OLD_HASHES = ("demo", "demo-v2", "demo-v3")
ALERT_RUN = {(5, 2), (5, 4), (5, 6)}  # (bundle, garment) collars with the skipped stitch the trend alert catches
FLAG_LABELS = ["puckering", "crooked_seam", "broken_stitch", "stain_damage", "skipped_stitch"]
OTHER = {"good": "crooked_seam", "skipped_stitch": "broken_stitch", "broken_stitch": "skipped_stitch",
         "crooked_seam": "puckering", "puckering": "good", "stain_damage": "good"}


def seeded_session(con):
    """Id of the current seeded demo session, or None."""
    row = con.execute("SELECT s.id FROM session s WHERE s.is_demo = 1 AND s.owner IS NULL AND EXISTS ("
                      "SELECT 1 FROM inspection i JOIN garment g ON g.id = i.garment_id JOIN bundle b ON b.id = g.bundle_id "
                      "WHERE b.session_id = s.id AND i.config_hash = ?) ORDER BY s.id LIMIT 1", (DEMO_HASH,)).fetchone()
    return row["id"] if row else None


def seed_demo(con, seed=7, date="2026-10-28"):
    """Create the demo session if the current one does not exist (replacing an older seed); returns its id."""
    sid = seeded_session(con)
    if sid:
        return sid
    marks = ", ".join("?" * len(OLD_HASHES))
    con.execute("DELETE FROM session WHERE is_demo = 1 AND owner IS NULL AND id IN (SELECT b.session_id FROM bundle b "
                f"JOIN garment g ON g.bundle_id = b.id JOIN inspection i ON i.garment_id = g.id WHERE i.config_hash IN ({marks}))",
                OLD_HASHES)
    con.commit()
    return _seed(con, random.Random(seed), date)


def _slots(plan):
    """Every inspection of the day in order: (bundle_no, garment_no, part, reason)."""
    out = []
    for b in range(1, BUNDLES + 1):
        for n in range(1, PER_BUNDLE + 1):
            spot = counts.spot_check_part(plan, CRITICAL, f"demo:{b}:{n}")
            out += [(b, n, p, "critical") for p in CRITICAL] + ([(b, n, spot, "spot_check")] if spot else [])
    return out


def _outcomes(slots, rng):
    """slot index -> (proposed_label, needs_checker, decision, final_label) for every slot that is not a plain good."""
    alert = [i for i, (b, n, p, _) in enumerate(slots) if p == "collar" and (b, n) in ALERT_RUN]
    free = [i for i in range(len(slots)) if i not in alert]
    # The checker's queue at the end of the day: recent flags, one per operation (so per machine) first
    recent = [i for i in free if slots[i][0] >= BUNDLES - 2]
    rng.shuffle(recent)
    by_part = {}
    for i in recent:
        by_part.setdefault(slots[i][2], i)
    pending = sorted(by_part.values())[:4]
    pending += [i for i in recent if i not in pending][:4 - len(pending)]
    rest = [i for i in free if i not in pending]
    overruled = rng.sample(rest, 8)  # sent to the checker, who said not a defect
    rest = [i for i in rest if i not in overruled]
    false_alarms = rng.sample(rest, 2)  # the AI was sure, the checker still said not a defect
    rest = [i for i in rest if i not in false_alarms]
    placket = next(i for i in rest if slots[i][2] == "placket" and slots[i][0] == 2)  # machine 12
    spot = next(i for i in rest if slots[i][3] == "spot_check" and slots[i][0] >= 3)
    out = {placket: ("broken_stitch", 0, "agree", "broken_stitch"), spot: ("stain_damage", 1, "agree", "stain_damage")}
    for k, i in enumerate(alert):
        if k == 1:  # the AI was unsure (broken or skipped); the checker changed it to skipped
            out[i] = ("broken_stitch", 1, "changed", "skipped_stitch")
        else:
            out[i] = ("skipped_stitch", 0, "agree", "skipped_stitch")
    for i, label in zip(pending, ["broken_stitch", "crooked_seam", "stain_damage", "puckering"]):
        out[i] = (label, 1, "pending", None)
    for i in overruled:
        out[i] = (rng.choice(FLAG_LABELS), 1, "not_a_defect", "good")
    for i in false_alarms:
        out[i] = (rng.choice(["puckering", "crooked_seam"]), 0, "not_a_defect", "good")
    return out


def _top2(label, rng):
    p = round(rng.uniform(0.55, 0.93), 3)
    return [[label, p], [OTHER.get(label, "good"), round((1 - p) * 0.7, 3)]]


def _seed(con, rng, date):
    sid = db.insert(con, "session", date=date, style="Men's shirt (demo)", fabric="A", lot_size=500, is_demo=1)
    for i, (part, op, mc) in enumerate(LINE_PLAN, 1):
        db.insert(con, "line_plan", session_id=sid, part=part, sequence_no=i, operator_id=op, machine_id=mc)
    plan = [dict(part=p, operator_id=o, machine_id=m) for p, o, m in LINE_PLAN]
    slots = _slots(plan)
    outcome = _outcomes(slots, rng)
    minute, k = 0, 0
    for bundle_no in range(1, BUNDLES + 1):
        bid = db.insert(con, "bundle", session_id=sid, bundle_no=bundle_no)
        for n in range(1, PER_BUNDLE + 1):
            spot = counts.spot_check_part(plan, CRITICAL, f"demo:{bundle_no}:{n}")
            gid = db.insert(con, "garment", bundle_id=bid, garment_no=n, spot_check_part=spot)
            while k < len(slots) and slots[k][:2] == (bundle_no, n):
                _, _, part, reason = slots[k]
                proposed, needs, decision, final = outcome.get(k, ("good", 0, "agree", "good"))
                op, mc = counts.map_part(plan, part)
                db.insert(con, "inspection", garment_id=gid, reason=reason, photo_path="demo", seam_found=1,
                          review_status="done", classifier_top2=_top2(proposed, rng), proposed_label=proposed,
                          proposed_severity=SEVERITY.get(proposed, "none"), needs_checker=needs, final_label=final,
                          final_part=part, final_severity=None if final is None else SEVERITY.get(final, "none"),
                          operator_id=op, machine_id=mc, decision=decision,
                          decided_via=None if decision == "pending" else "tap", config_hash=DEMO_HASH)
                k += 1
            minute += 3 + rng.randrange(3)
            db.update(con, "garment", gid, status="complete", completed_at=f"{date}T{9 + minute // 60:02d}:{minute % 60:02d}:00")
            _alerts(con, sid)  # as the app does when a garment is completed
    return sid


def _alerts(con, sid):
    for machine, defect, garments in counts.trend_alerts(db.session_inspections(con, sid), db.completed_garments(con, sid)):
        seen = con.execute("SELECT garment_ids FROM alert WHERE session_id = ? AND machine_id = ? AND defect = ?",
                           (sid, machine, defect)).fetchall()
        if not any(set(garments) <= set(a["garment_ids"]) for a in seen):
            db.insert(con, "alert", session_id=sid, machine_id=machine, defect=defect, garment_ids=garments)
