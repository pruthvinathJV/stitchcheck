from stitchcheck.spark.inspect import ASK_CHECKER, hybrid_rule, parse_verdict, severity


def v(label, verdict="override"):
    return {"verdict": verdict, "label": label}


def test_agreement_is_auto_labelled():
    assert hybrid_rule([("broken_stitch", 0.9), ("good", 0.05)], v("broken_stitch", "confirm")) == ("broken_stitch", "broken_stitch", True)


def test_disagreement_goes_to_checker_with_sparks_proposal():
    assert hybrid_rule([("good", 0.7), ("stain_damage", 0.2)], v("stain_damage")) == (ASK_CHECKER, "stain_damage", False)


def test_protected_major_cannot_be_proposed_as_good_or_crooked():
    for s in ("good", "crooked_seam"):
        assert hybrid_rule([("skipped_stitch", 0.6), ("crooked_seam", 0.3)], v(s)) == (ASK_CHECKER, "skipped_stitch", True)


def test_protected_major_can_become_another_defect_on_review():
    assert hybrid_rule([("stain_damage", 0.8), ("good", 0.1)], v("broken_stitch")) == (ASK_CHECKER, "broken_stitch", True)


def test_below_threshold_is_not_protected():
    assert hybrid_rule([("skipped_stitch", 0.59), ("good", 0.3)], v("good")) == (ASK_CHECKER, "good", False)


def test_spark_ask_checker_and_missing_verdict():
    assert hybrid_rule([("good", 0.9), ("crooked_seam", 0.05)], v("good", "ask_checker"))[0] == ASK_CHECKER
    assert hybrid_rule([("good", 0.9), ("crooked_seam", 0.05)], None) == (ASK_CHECKER, "good", False)


def test_parse_verdict_maps_other_to_unknown_and_rejects_bad_labels():
    assert parse_verdict('{"verdict": "unknown_defect", "label": "other"}')["label"] == "unknown_defect"
    assert parse_verdict('{"verdict": "confirm", "label": "hole"}') is None
    assert parse_verdict("no json") is None


def test_severity_fixed_map_and_spark_judgement():
    assert severity("broken_stitch", {"severity_judgement": "minor"}) == "major"
    assert severity("stain_damage", {"severity_judgement": "minor"}) == "minor"
    assert severity("crooked_seam", None) == "major"
    assert severity("good", None) == "none"
