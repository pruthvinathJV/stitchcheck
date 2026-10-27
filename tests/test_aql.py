import pytest

from stitchcheck.qc.aql import aql_plan, audit_status, code_letter


def plan_tuple(p, severity):
    s = p[severity]
    return s["sample_size"], s["ac"], s["re"]


@pytest.mark.parametrize("lot, code, n, major, minor", [
    (281, "H", 50, (3, 4), (5, 6)), (500, "H", 50, (3, 4), (5, 6)),
    (501, "J", 80, (5, 6), (7, 8)), (1200, "J", 80, (5, 6), (7, 8)),
    (1201, "K", 125, (7, 8), (10, 11)), (3200, "K", 125, (7, 8), (10, 11)),
    (3201, "L", 200, (10, 11), (14, 15)), (10000, "L", 200, (10, 11), (14, 15)),
])
def test_anchor_rows(lot, code, n, major, minor):
    p = aql_plan(lot)
    assert p["code"] == code
    assert plan_tuple(p, "major") == (n, *major)
    assert plan_tuple(p, "minor") == (n, *minor)


def test_brief_example_lot_2400():
    assert plan_tuple(aql_plan(2400), "major") == (125, 7, 8)


@pytest.mark.parametrize("lot, code", [(2, "A"), (8, "A"), (9, "B"), (15, "B"), (16, "C"), (25, "C"), (26, "D"),
                                       (50, "D"), (51, "E"), (90, "E"), (91, "F"), (150, "F"), (151, "G"),
                                       (280, "G"), (35000, "M"), (35001, "N"), (150001, "P"), (500001, "Q")])
def test_code_letter_boundaries(lot, code):
    assert code_letter(lot) == code


@pytest.mark.parametrize("lot, major, minor", [
    (12, (5, 0, 1), (3, 0, 1)),     # B: major arrow down to C; minor 0/1 at B
    (20, (5, 0, 1), (3, 0, 1)),     # C: major 0/1 at C; minor arrow up to B
    (40, (5, 0, 1), (13, 1, 2)),    # D: major arrow up to C; minor arrow down to E
    (60, (20, 1, 2), (13, 1, 2)),   # E: major arrow down to F; minor 1/2 at E
    (100000, (500, 21, 22), (315, 21, 22)),  # N: minor arrow up to M
    (300000, (500, 21, 22), (315, 21, 22)),  # P: both arrows up
])
def test_arrows_give_per_severity_sample_sizes(lot, major, minor):
    p = aql_plan(lot)
    assert plan_tuple(p, "major") == major
    assert plan_tuple(p, "minor") == minor


def test_sample_reaching_lot_size_means_full_inspection():
    p = aql_plan(4)  # A: major plan n=5, minor n=3
    assert p["major"]["sample_size"] == 4 and p["major"]["full_inspection"]
    assert p["minor"]["sample_size"] == 3 and not p["minor"]["full_inspection"]


def test_lot_below_two_is_rejected():
    with pytest.raises(ValueError):
        code_letter(1)


def test_audit_rejects_as_soon_as_re_is_reached():
    p = aql_plan(2400)  # major 7/8, n = 125
    findings = [{"major": 1}] * 8
    s = audit_status(p, findings)
    assert s["status"] == "reject" and s["rejected_by"] == ["major"] and s["inspected"] == 8


def test_audit_in_progress_then_accept_at_ac():
    p = aql_plan(2400)
    clean = [{}] * 118 + [{"major": 1}] * 7
    assert audit_status(p, clean[:100])["status"] == "in_progress"
    assert audit_status(p, clean)["status"] == "accept"


def test_any_critical_rejects():
    assert audit_status(aql_plan(2400), [{"critical": 1}])["rejected_by"] == ["critical"]


def test_each_severity_counts_only_its_own_sample():
    p = aql_plan(40)  # major n=5 (0/1), minor n=13 (1/2)
    findings = [{}] * 5 + [{"major": 1}] + [{}] * 7  # a major on garment 6 is outside the major sample
    s = audit_status(p, findings)
    assert s["counts"]["major"] == 0 and s["status"] == "accept"
