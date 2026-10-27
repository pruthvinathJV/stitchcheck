"""Counting on the line: DHU, defective %, line-plan mapping, spot checks and audit samples (pure functions).

Inspections are dicts with at least garment_id, decision and final_label (and the breakdown fields: operator_id,
machine_id, final_part). Only checker-confirmed defects count: decision agree or changed with a defect label.
A garment counts as a unit once it is completed, whether or not a defect was found.
"""
import random
from collections import Counter, defaultdict

CONFIRMED = ("agree", "changed")
NOT_DEFECTS = ("good", None, "")
BREAKDOWNS = {"operator": "operator_id", "machine": "machine_id", "part": "final_part", "defect": "final_label"}


def confirmed_defects(inspections, completed):
    """Confirmed defects on completed garments."""
    completed = set(completed)
    return [i for i in inspections if i["decision"] in CONFIRMED and i["final_label"] not in NOT_DEFECTS
            and i["garment_id"] in completed]


def dhu(inspections, completed):
    """Defects per hundred units: confirmed defects / completed garments x 100 (0 with no completed garments)."""
    completed = set(completed)
    return 100 * len(confirmed_defects(inspections, completed)) / len(completed) if completed else 0.0


def defective_pct(inspections, completed):
    """Completed garments with at least one confirmed defect / completed garments x 100."""
    completed = set(completed)
    bad = {i["garment_id"] for i in confirmed_defects(inspections, completed)}
    return 100 * len(bad) / len(completed) if completed else 0.0


def dhu_by(inspections, completed, by):
    """DHU split by operator, machine, part or defect: {key: {"defects": n, "dhu": x}}, worst first. The
    denominator is every completed garment, so the parts add up to the session DHU."""
    completed = set(completed)
    counts = Counter(i[BREAKDOWNS[by]] for i in confirmed_defects(inspections, completed))
    n = len(completed)
    return {k: dict(defects=c, dhu=100 * c / n if n else 0.0) for k, c in counts.most_common()}


def map_part(line_plan, part):
    """The operator and machine responsible for a part: line_plan rows are dicts with part, operator_id,
    machine_id. Returns (None, None) if the part is not in the plan, so the checker picks them."""
    for row in line_plan:
        if row["part"] == part:
            return row["operator_id"], row["machine_id"]
    return None, None


def spot_check_part(line_plan, critical_parts, garment_key, rate=0.1, seed=0):
    """For 1 garment in 1/rate (drawn per garment, reproducibly), one random part that is not a critical part;
    otherwise None."""
    rng = random.Random(f"{seed}:{garment_key}")
    if rng.random() >= rate:
        return None
    others = sorted({row["part"] for row in line_plan} - set(critical_parts))
    return rng.choice(others) if others else None


def audit_sample(lot_size, n, seed):
    """Garment numbers (1..lot_size) to pull for an audit, in draw order: each severity uses the first n of its
    own plan, so the draw covers the largest sample size."""
    return random.Random(seed).sample(range(1, lot_size + 1), min(n, lot_size))


def worst_combinations(inspections, completed, n=3):
    """Top operator-part-defect combinations by confirmed count: [(operator, part, defect, count)]."""
    counts = Counter((i["operator_id"], i["final_part"], i["final_label"]) for i in confirmed_defects(inspections, completed))
    return [(*k, c) for k, c in counts.most_common(n)]


def process_issues(inspections, completed, n=3):
    """Top machine-part-defect combinations by confirmed count: [(machine, part, defect, count)]. Process first:
    the issue is the machine and the operation, not the person."""
    c = Counter((i["machine_id"], i["final_part"], i["final_label"]) for i in confirmed_defects(inspections, completed))
    return [(*k, v) for k, v in c.most_common(n)]


TREND_THRESHOLD, TREND_WINDOW = 3, 10


def trend_alerts(inspections, garment_order, threshold=TREND_THRESHOLD, window=TREND_WINDOW):
    """Machines with the same confirmed defect on at least `threshold` of the last `window` completed garments in
    which that machine's work was inspected. garment_order: completed garment ids, oldest first.
    Returns [(machine_id, defect, [garment_ids])]."""
    machines_of = defaultdict(set)
    for i in inspections:
        if i.get("machine_id"):
            machines_of[i["garment_id"]].add(i["machine_id"])
    by_machine = defaultdict(list)
    for g in garment_order:
        for m in machines_of[g]:
            by_machine[m].append(g)
    defects = confirmed_defects(inspections, garment_order)
    alerts = []
    for m, garments in by_machine.items():
        recent = set(garments[-window:])
        hits = defaultdict(set)
        for i in defects:
            if i["machine_id"] == m and i["garment_id"] in recent:
                hits[i["final_label"]].add(i["garment_id"])
        alerts += [(m, d, sorted(gs)) for d, gs in hits.items() if len(gs) >= threshold]
    return alerts


def defects_per_unit(inspections, completed):
    """Confirmed major and minor defects per completed garment (critical counted as major for this rate)."""
    completed = set(completed)
    d = confirmed_defects(inspections, completed)
    n = len(completed) or 1
    major = sum(1 for i in d if i.get("final_severity") in ("major", "critical"))
    minor = sum(1 for i in d if i.get("final_severity") == "minor")
    return major / n, minor / n
