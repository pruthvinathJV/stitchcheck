import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "techpack"))
from score import score_sheet, wilson  # noqa: E402

from stitchcheck.techpack.extract import _level, map_part, validate  # noqa: E402


def field(value, status="ok", source="x"):
    return {"value": value, "status": status, "source": source}


def plan(**over):
    p = {"header": {k: field("v") for k in ["buyer", "style_no", "style_name", "fabric", "color", "thread"]},
         "operations": [{"part_as_written": "Collar band", "operation": "Band attach", "stitch_type": "301", "spi_target": 14,
                         "spi_tolerance": 1, "seam_allowance_mm": 6, "critical": True, "status": "ok", "source": "row 1"},
                        {"part_as_written": "Yoke", "operation": "Yoke attach", "stitch_type": "301", "spi_target": 12,
                         "spi_tolerance": 1, "seam_allowance_mm": 10, "critical": None, "status": "ok", "source": "row 2"}],
         "aql": {"inspection_level": field("General Inspection Level II"), "critical": field(0),
                 "major": field(2.5), "minor": field(4.0)},
         "severity_rules": [], "general_notes": [], "handwritten_changes": [], "out_of_scope": ["measurements"]}
    p.update(over)
    return p


def test_part_synonyms_are_exact_and_never_nearest():
    assert map_part("Collar band") == "collar" and map_part("Armhole (sleeve attach)") == "sleeve"
    assert map_part("Front placket") == "placket" and map_part("Side seam") == "side_seam"
    assert map_part("Yoke") is None and map_part("Collar point") is None


def test_levels():
    assert _level("General Inspection Level II") == "II" and _level("Level III") == "III" and _level("S-3") == "S-3"
    assert _level("Level 2") is None


def test_validation_flags_unsupported_out_of_range_unmapped_and_missing():
    p = plan()
    p["aql"]["major"] = field(1.5)
    p["aql"]["minor"] = field(6.5)
    p["operations"][1]["spi_target"] = 40
    p["operations"][1]["stitch_type"] = "lockstitch"
    p["header"]["thread"] = field(None, "missing", "TBC")
    v = validate(p)
    assert v["unsupported"] == ["aql.major=1.5 (only 2.5 is supported)", "aql.minor=6.5 (only 4.0 is supported)"]
    assert v["unmapped_parts"] == ["Yoke"] and v["missing"] == ["header.thread (missing)"]
    assert any("spi_target=40" in e for e in v["errors"]) and any("ISO 4915" in e for e in v["errors"])
    p["aql"]["major"] = field(3.0)
    assert any("not an ISO 2859-1 AQL value" in e for e in validate(p)["errors"])


def test_scoring_counts_correct_fields_hallucinated_rows_and_flags():
    gt = {"sheet_id": "X", "buyer": "v", "style_no": "v", "style_name": "v", "fabric": "v", "color": "v", "thread": None,
          "operations": [{"part": "collar_band", "operation": "Band attach", "stitch_type": "301", "spi_target": 14,
                          "spi_tolerance": 1, "seam_allowance_mm": 6, "critical": True}],
          "aql": {"inspection_level": "II", "critical": 0, "major": 2.5, "minor": 4.0},
          "severity_rules": [{"defect": "stain_damage", "severity": "major / minor"}],
          "missing_or_unreadable": ["thread (TBC)"], "unsupported_by_app": []}
    p = plan(severity_rules=[{"text_as_written": "t", "meaning_en": "t", "defect": "stain_damage", "location": "front",
                              "severity": "minor", "source": "s"}])
    _, s = score_sheet(gt, {"plan": copy.deepcopy(p)})
    # header: 5 right, thread wrong (value given where the sheet has none); aql 4 right; op: found + 5 fields right
    assert (s["fields"], s["correct"]) == (16, 15)
    assert s["hallucinations"] == 2  # the thread value and the Yoke row
    assert s["rules_matched"] == 1 and s["missing_flagged"] == 0
    p["header"]["thread"] = field(None, "missing", "TBC")
    _, s = score_sheet(gt, {"plan": copy.deepcopy(p)})
    assert s["correct"] == 16 and s["missing_flagged"] == 1 and s["hallucinations"] == 1


def test_wilson():
    lo, hi = wilson(90, 100)
    assert 0.82 < lo < 0.84 and 0.94 < hi < 0.95
