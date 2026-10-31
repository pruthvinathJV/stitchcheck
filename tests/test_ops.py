"""Pure helpers of the ops scripts: smoke test checks and table, load test statistics, deploy and backup rules."""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import load_test  # noqa: E402
import smoke_e2e  # noqa: E402
from smoke_e2e import Result  # noqa: E402


def load_serve_modal():
    spec = importlib.util.spec_from_file_location("serve_modal", ROOT / "scripts" / "cloud" / "serve_modal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def dash(dhu, completed, defects):
    return {"dhu": dhu, "completed_garments": completed, "by": {"defect": {k: {"defects": n, "dhu": 0} for k, n in defects.items()}}}


def test_dhu_check_recomputes_from_the_defect_breakdown():
    assert smoke_e2e.dhu_check(dash(26.19, 42, {"skipped_stitch": 6, "broken_stitch": 3, "stain_damage": 2})) == (True, 26.19, 26.19)
    ok, expected, got = smoke_e2e.dhu_check(dash(20.0, 42, {"skipped_stitch": 11}))
    assert not ok and expected == 26.19 and got == 20.0
    assert smoke_e2e.dhu_check(dash(0.0, 0, {}))[0]  # no completed garments: DHU is 0
    assert not smoke_e2e.dhu_check(dash(5.0, 0, {}))[0]


def test_part_comes_from_the_photo_name():
    assert smoke_e2e.part_for("data/smoke/shirt/pocket.jpeg") == "pocket"
    assert smoke_e2e.part_for("Side_Seam.JPG") == "side_seam"
    assert smoke_e2e.part_for("full.jpeg") == "collar"


def test_exit_code_counts_only_required_failures():
    assert smoke_e2e.exit_code([Result("health", True), Result("version", None, note="not deployed yet")]) == 0
    assert smoke_e2e.exit_code([Result("health", True), Result("gallery", False, required=False)]) == 0
    assert smoke_e2e.exit_code([Result("health", True), Result("review", False)]) == 1


def test_result_table_aligns_columns_and_marks_results():
    table = smoke_e2e.format_table([Result("health", True, 12, "HTTP 200"), Result("dashboard_dhu", False, 1234, "DHU 3"),
                                    Result("report", None, 0, "--no-ai"), Result("gallery", False, 5, "HTTP 500", False)])
    lines = table.splitlines()
    assert lines[0].split() == ["step", "result", "ms", "note"] and set(lines[1].replace(" ", "")) == {"-"}
    assert lines[2].split()[:3] == ["health", "ok", "12"]
    assert lines[3].split()[:3] == ["dashboard_dhu", "FAIL", "1234"]
    assert lines[4].split()[:2] == ["report", "skip"] and lines[5].split()[1] == "warn"
    assert len({line.index(line.split()[2]) + len(line.split()[2]) for line in lines[2:]}) == 1  # ms right-aligned


def test_endpoint_templates_and_percentiles():
    assert load_test.endpoint_of("POST", "/garments/12/photos") == "POST /garments/{id}/photos"
    assert load_test.endpoint_of("GET", "/sessions/3/dashboard?x=1") == "GET /sessions/{id}/dashboard"
    assert load_test.endpoint_of("GET", "/brains") == "GET /brains"
    values = list(range(1, 101))
    assert load_test.percentile(values, 50) == 50 and load_test.percentile(values, 95) == 95
    assert load_test.percentile([7], 95) == 7 and load_test.percentile([3, 1, 2], 100) == 3


def test_summary_and_failure_reasons():
    Rec = load_test.Rec
    recs = [Rec("GET /x", 10, 200), Rec("GET /x", 30, 200), Rec("GET /x", 20, 404, "not found"), Rec("POST /y", 5, 201)]
    s = load_test.summarize(recs)
    assert s["GET /x"] == {"n": 3, "p50": 20, "p95": 30, "max": 30, "errors": 1} and s["POST /y"]["errors"] == 0
    assert load_test.must_fail(recs, []) == []  # a 404 is reported but does not fail the run
    reasons = load_test.must_fail(recs + [Rec("GET /x", 1, 500, "boom"), Rec("GET /x", 1, None, "reset")],
                                  ["sqlite3.OperationalError: database is locked"])
    assert len(reasons) == 3 and any("locked" in r for r in reasons)
    assert load_test.must_fail([Rec("GET /i", 0, 200, "still reviewing after 30 s")], [])


def test_deploy_refuses_changes_outside_app_only():
    m = load_serve_modal()
    porcelain = " M app/index.html\n?? app/new.js\n M src/stitchcheck/backend/app.py\nR  old.py -> scripts/new.py\n?? \"docs/a b.md\""
    assert m.dirty_outside_app(porcelain) == ["src/stitchcheck/backend/app.py", "scripts/new.py", "docs/a b.md"]
    assert m.dirty_outside_app(" M app/sw.js\n") == [] and m.dirty_outside_app("") == []
    assert m.dirty_outside_app(" M apps.txt") == ["apps.txt"]


def test_backup_names_and_pruning_keep_the_newest_seven():
    m = load_serve_modal()
    assert m.backup_name(datetime(2026, 11, 5, 3, 0)) == "stitchcheck-20261105.db"
    names = [f"stitchcheck-202611{d:02d}.db" for d in range(1, 11)] + ["backup.jsonl"]
    assert m.backups_to_prune(names) == ["stitchcheck-20261101.db", "stitchcheck-20261102.db", "stitchcheck-20261103.db"]
    assert m.backups_to_prune(names[:7]) == []


def test_refuse_dirty_deploy_raises_unless_allowed(monkeypatch):
    m = load_serve_modal()
    monkeypatch.setattr(m, "git", lambda *a: " M src/x.py")
    monkeypatch.delenv("STITCHCHECK_ALLOW_DIRTY", raising=False)
    with pytest.raises(SystemExit, match="src/x.py"):
        m.refuse_dirty_deploy()
    monkeypatch.setenv("STITCHCHECK_ALLOW_DIRTY", "1")
    m.refuse_dirty_deploy()
