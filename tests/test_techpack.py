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


GT_SEEN = Path(__file__).parents[1] / "data" / "techpack" / "ground_truth_seen.json"


def perfect(gt):
    """The extraction a flawless reader would return for a ground-truth sheet."""
    import json as _json
    f = lambda v: field(v, "ok" if v is not None else "missing")
    op = lambda o: {"part_as_written": o["part"].replace("_", " "), "operation": o["operation"], "stitch_type": o["stitch_type"],
                    "spi_target": o["spi_target"], "spi_tolerance": o["spi_tolerance"], "seam_allowance_mm": o["seam_allowance_mm"],
                    "critical": o["critical"], "status": "ok", "source": "row"}
    sev = lambda r, s: {"text_as_written": r["text"], "meaning_en": r.get("meaning", r["text"]), "defect": r["defect"],
                        "location": r["location"], "severity": s, "source": "rule"}
    return {"plan": _json.loads(_json.dumps({
        "header": {k: f(gt[k]) for k in ["buyer", "style_no", "style_name", "fabric", "color", "thread"]},
        "operations": [op(o) for o in gt["operations"]],
        "aql": {"inspection_level": field(f"General Inspection Level {gt['aql']['inspection_level']}"),
                **{k: field(gt["aql"][k]) for k in ("critical", "major", "minor")}},
        "severity_rules": [sev(r, r["severity"].split("/")[0].strip()) for r in gt["severity_rules"]],
        "general_notes": [], "handwritten_changes": [{"field": c, "printed": "", "written": "", "source": ""} for c in gt.get("handwritten_corrections", [])],
        "out_of_scope": gt["out_of_scope"]}))}


def test_a_perfect_extraction_scores_full_marks_on_every_seen_sheet():
    import json as _json
    for gt in _json.loads(GT_SEEN.read_text()):
        df, s = score_sheet(gt, perfect(gt))
        assert s["correct"] == s["fields"] and s["hallucinations"] == 0, (gt["sheet_id"], df[~df["correct"]])
        assert s["missing_flagged"] == s["missing"] and s["unsupported_flagged"] == s["unsupported"]
        assert s["rules_matched"] == s["rules"] and s["handwritten_listed"] == s["handwritten"]


def test_values_where_the_sheet_gives_none_are_hallucinations_not_wrong_values():
    import json as _json
    gt = next(g for g in _json.loads(GT_SEEN.read_text()) if g["sheet_id"] == "TP-05")  # thread is TBC
    ext = perfect(gt)
    ext["plan"]["header"]["thread"] = field("Tex 40")      # invented
    ext["plan"]["operations"][0]["spi_target"] = 12        # wrong (sheet says 10)
    ext["plan"]["operations"][1]["seam_allowance_mm"] = None  # omitted
    gt = _json.loads(_json.dumps(gt))
    gt["operations"][2]["critical"] = None                 # pretend the sheet leaves this blank
    _, s = score_sheet(gt, ext)
    assert (s["hallucinated_fields"], s["wrong"], s["omitted"]) == (2, 1, 1)
    assert any("header.thread" in h for h in s["hallucination_items"]) and any("critical" in h for h in s["hallucination_items"])


def test_cli_takes_an_unseen_truth_file_with_no_code_changes(tmp_path, monkeypatch):
    import json as _json
    import score as score_mod
    seen = _json.loads(GT_SEEN.read_text())
    unseen = [dict(seen[0], sheet_id="TP-06"), dict(seen[3], sheet_id="TP-07")]  # same shape as the seen file
    (tmp_path / "ground_truth_unseen.json").write_text(_json.dumps(unseen))
    ext_dir = tmp_path / "extractions"
    ext_dir.mkdir()
    for g in seen + unseen:
        (ext_dir / f"{g['sheet_id']}.json").write_text(_json.dumps(perfect(g)))
    monkeypatch.setattr(score_mod, "EXTRACTIONS", ext_dir)
    monkeypatch.setattr(score_mod, "OUT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["score", "--truth", str(GT_SEEN), str(tmp_path / "ground_truth_unseen.json")])
    score_mod.main()
    summary = _json.loads((tmp_path / "score_summary.json").read_text())
    assert summary["unseen"]["sheets"] == ["TP-06", "TP-07"] and summary["unseen"]["field_accuracy"] == 1.0
    assert summary["seen"]["hallucinations"] == 0 and summary["unseen"]["hallucinated_values"] == 0
