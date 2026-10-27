"""AQL audit plans: ISO 2859-1 single sampling, normal inspection, General Inspection Level II.

Major defects use AQL 2.5 and minor defects AQL 4.0; critical defects accept 0 and are set by the checker.
Audits count defects (nonconformities), not defective garments.

Tables checked cell by cell (code letters for Level II, and every AQL 2.5 and 4.0 entry including the arrows)
against two independent public reproductions of ISO 2859-1 Table 1 and Table 2-A, Oct 27 2026:
  - QCAdvisor, "Sample size table (ISO 2859-1)" and "Single sampling plan for normal inspection (ISO 2859-1)",
    https://www.qcadvisor.com/wp-content/uploads/2023/05/AQL-QCADVISOR.pdf
  - V-Trust, "Sample size code letters" and "Single sampling plans for normal inspection",
    https://chs.v-trust.com/en/our-services/inspection-standards/aql-table
Arrows are resolved as the standard says: use the first plan below (down) or above (up) the arrow, which can give
major and minor different sample sizes. If the sample size reaches the lot size, every garment is inspected.
"""
from bisect import bisect_left

# Table 1, General Inspection Level II: upper bound of each lot-size range -> code letter.
LOT_UPPER = [8, 15, 25, 50, 90, 150, 280, 500, 1200, 3200, 10000, 35000, 150000, 500000]
LEVEL_II = "ABCDEFGHJKLMNPQ"  # the last letter is for lots of 500,001 and over
SAMPLE_SIZE = dict(A=2, B=3, C=5, D=8, E=13, F=20, G=32, H=50, J=80, K=125, L=200, M=315, N=500, P=800, Q=1250, R=2000)
ORDER = "ABCDEFGHJKLMNPQR"

# Table 2-A columns as printed: (Ac, Re), or "down" / "up" for the arrows.
TABLE_2A = {
    2.5: dict(A="down", B="down", C=(0, 1), D="up", E="down", F=(1, 2), G=(2, 3), H=(3, 4), J=(5, 6), K=(7, 8),
              L=(10, 11), M=(14, 15), N=(21, 22), P="up", Q="up", R="up"),
    4.0: dict(A="down", B=(0, 1), C="up", D="down", E=(1, 2), F=(2, 3), G=(3, 4), H=(5, 6), J=(7, 8), K=(10, 11),
              L=(14, 15), M=(21, 22), N="up", P="up", Q="up", R="up"),
}
AQL = {"major": 2.5, "minor": 4.0}


def code_letter(lot_size):
    if lot_size < 2:
        raise ValueError("lot size must be at least 2")
    return LEVEL_II[bisect_left(LOT_UPPER, lot_size)]


def resolve(aql, letter):
    """Follow the arrows from a code letter to the plan the standard says to use: (letter, Ac, Re)."""
    i = ORDER.index(letter)
    while isinstance(TABLE_2A[aql][ORDER[i]], str):
        i += 1 if TABLE_2A[aql][ORDER[i]] == "down" else -1
    ac, re = TABLE_2A[aql][ORDER[i]]
    return ORDER[i], ac, re


def aql_plan(lot_size):
    """{code, lot_size, major: {aql, sample_size, ac, re, full_inspection}, minor: {...}, critical: {...}}."""
    letter = code_letter(lot_size)
    plan = dict(code=letter, lot_size=lot_size)
    for severity, aql in AQL.items():
        used, ac, re = resolve(aql, letter)
        n = SAMPLE_SIZE[used]
        plan[severity] = dict(aql=aql, plan_letter=used, sample_size=min(n, lot_size), ac=ac, re=re,
                              full_inspection=n >= lot_size)
    plan["critical"] = dict(sample_size=max(plan["major"]["sample_size"], plan["minor"]["sample_size"]), ac=0, re=1)
    return plan


def audit_status(plan, findings):
    """findings: one dict per sampled garment, in draw order: {"major": n, "minor": n, "critical": n} defect counts.
    Each severity counts only the first sample_size garments of the draw. Returns status (in_progress / accept /
    reject), the counts per severity, and which severity rejected."""
    counts, done, rejected = {}, True, []
    for severity in ("critical", "major", "minor"):
        n = plan[severity]["sample_size"]
        counts[severity] = sum(f.get(severity, 0) for f in findings[:n])
        if counts[severity] >= plan[severity]["re"]:
            rejected.append(severity)
        elif len(findings) < n:
            done = False
    status = "reject" if rejected else "accept" if done else "in_progress"
    return dict(status=status, counts=counts, rejected_by=rejected, inspected=len(findings),
                sample_size=plan["critical"]["sample_size"])


def _poisson_cdf(k, mean):
    import math
    term = total = math.exp(-mean)
    for i in range(1, k + 1):
        term *= mean / i
        total += term
    return min(total, 1.0)


def pass_chance(plan, major_per_unit, minor_per_unit):
    """Chance that an audit of this plan accepts the lot, if defects occur at these rates per garment: the defects
    in each severity's sample are taken as Poisson (defects are counted, not defective garments), and the lot passes
    when both stay at or under Ac. Critical defects are not modelled (any one rejects)."""
    out = {}
    for sev, rate in (("major", major_per_unit), ("minor", minor_per_unit)):
        p = plan[sev]
        out[sev] = round(_poisson_cdf(p["ac"], p["sample_size"] * rate), 3)
    out["both"] = round(out["major"] * out["minor"], 3)
    return out
