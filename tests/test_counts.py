from stitchcheck.qc.counts import (audit_sample, defective_pct, dhu, dhu_by, map_part, spot_check_part, trend_alerts,
                                   worst_combinations)

PLAN = [dict(part="collar", operator_id="OP01", machine_id="M01"), dict(part="placket", operator_id="OP02", machine_id="M02"),
        dict(part="cuff", operator_id="OP03", machine_id="M03"), dict(part="hem", operator_id="OP04", machine_id="M04")]


def insp(g, label, decision="agree", part="collar", op="OP01", m="M01"):
    return dict(garment_id=g, final_label=label, decision=decision, final_part=part, operator_id=op, machine_id=m)


def test_dhu_counts_defects_not_garments():
    rows = [insp(1, "broken_stitch"), insp(1, "stain_damage", part="placket", op="OP02", m="M02"), insp(2, "good")]
    assert dhu(rows, completed=[1, 2, 3, 4]) == 50.0       # 2 defects / 4 garments
    assert defective_pct(rows, completed=[1, 2, 3, 4]) == 25.0  # 1 defective garment / 4


def test_only_confirmed_defects_on_completed_garments_count():
    rows = [insp(1, "broken_stitch", decision="pending"), insp(2, "skipped_stitch", decision="not_a_defect"),
            insp(3, "crooked_seam", decision="changed"), insp(4, "stain_damage"), insp(5, "good", decision="changed")]
    assert dhu(rows, completed=[1, 2, 3, 5]) == 25.0  # only garment 3; garment 4 is not completed


def test_no_completed_garments_gives_zero():
    assert dhu([], []) == 0.0 and defective_pct([], []) == 0.0


def test_breakdowns_add_up_to_session_dhu():
    rows = [insp(1, "broken_stitch"), insp(2, "broken_stitch"), insp(2, "stain_damage", part="placket", op="OP02", m="M02")]
    by_op = dhu_by(rows, [1, 2, 3, 4], "operator")
    assert list(by_op) == ["OP01", "OP02"] and by_op["OP01"] == dict(defects=2, dhu=50.0)
    assert sum(v["dhu"] for v in by_op.values()) == dhu(rows, [1, 2, 3, 4])
    assert dhu_by(rows, [1, 2, 3, 4], "defect")["broken_stitch"]["defects"] == 2


def test_map_part():
    assert map_part(PLAN, "placket") == ("OP02", "M02")
    assert map_part(PLAN, "pocket") == (None, None)


def test_spot_check_rate_and_never_a_critical_part():
    picks = [spot_check_part(PLAN, ["collar", "placket"], g) for g in range(2000)]
    chosen = [p for p in picks if p]
    assert 0.08 < len(chosen) / len(picks) < 0.12
    assert set(chosen) <= {"cuff", "hem"}
    assert picks == [spot_check_part(PLAN, ["collar", "placket"], g) for g in range(2000)]  # reproducible


def test_audit_sample_is_unique_in_range_and_reproducible():
    s = audit_sample(2400, 125, seed=7)
    assert len(s) == len(set(s)) == 125 and min(s) >= 1 and max(s) <= 2400
    assert s == audit_sample(2400, 125, seed=7)
    assert sorted(audit_sample(3, 5, seed=1)) == [1, 2, 3]


def test_worst_combinations():
    rows = [insp(1, "broken_stitch"), insp(2, "broken_stitch"), insp(3, "stain_damage", part="placket", op="OP02", m="M02")]
    assert worst_combinations(rows, [1, 2, 3], n=1) == [("OP01", "collar", "broken_stitch", 2)]


def test_trend_alert_fires_at_three_in_last_ten():
    rows = [insp(g, "skipped_stitch" if g in (12, 15, 18) else "good") for g in range(1, 21)]
    assert trend_alerts(rows, list(range(1, 21))) == [("M01", "skipped_stitch", [12, 15, 18])]


def test_trend_alert_ignores_defects_outside_the_window_and_other_machines():
    rows = [insp(g, "skipped_stitch" if g in (2, 15, 18) else "good") for g in range(1, 21)]
    rows += [insp(19, "skipped_stitch", part="cuff", op="OP03", m="M03")]
    assert trend_alerts(rows, list(range(1, 21))) == []
