import json

import pytest

from stitchcheck.backend.db import connect, insert, update
from stitchcheck.backend.report import Tools, render, run_report, validate

FACTS = {"dhu.session.dhu": "12.5", "dhu.operator.OP07.dhu": "30.0", "top.1.machine": "12"}


def test_valid_template_passes_and_renders():
    t = "## Buyer summary\nDHU {{dhu.session.dhu}} at AQL 2.5 (ISO 2859-1). Operator {{ dhu.operator.OP07.dhu }}."
    assert validate(t, FACTS) == []
    assert render(t, FACTS).endswith("DHU 12.5 at AQL 2.5 (ISO 2859-1). Operator 30.0.")


@pytest.mark.parametrize("bad, expect", [
    ("DHU was 12.5 today.", "number typed"),
    ("Check machine {{top.1.machine}} and OP07.", "ID typed"),
    ("DHU {{dhu.session.made_up}}", "unknown placeholder"),
    ("మొత్తం ౧౨ లోపాలు", "number typed"),  # Telugu digits count too
])
def test_invented_numbers_ids_and_paths_fail(bad, expect):
    assert any(expect in e for e in validate(bad, FACTS))


@pytest.fixture
def session(tmp_path):
    con = connect(tmp_path / "t.db")
    s = insert(con, "session", date="2026-10-28", style="shirt", fabric="A", lot_size=500)
    b = insert(con, "bundle", session_id=s, bundle_no=1)
    for n, label in enumerate(["skipped_stitch", "skipped_stitch", "good", None], 1):
        g = insert(con, "garment", bundle_id=b, garment_no=n)
        if label:
            i = insert(con, "inspection", garment_id=g, reason="critical", photo_path="p", seam_found=1, config_hash="x")
            update(con, "inspection", i, decision="agree", final_label=label, final_part="collar", operator_id="OP07",
                   machine_id="12")
        update(con, "garment", g, status="complete", completed_at=f"2026-10-28T10:0{n}:00")
    return con, s


def test_tools_expose_facts_from_the_database(session):
    con, s = session
    t = Tools(con, s)
    assert t.get_dhu("session")["facts"] == {"dhu.session.dhu": "50.0", "dhu.session.defective_pct": "50.0",
                                             "dhu.session.garments": "4", "dhu.session.defects": "2"}
    assert t.get_dhu("machine")["facts"]["dhu.machine.12.defects"] == "2"
    assert t.get_top_issues(1)["facts"] == {"top.1.operator": "OP07", "top.1.part": "collar", "top.1.defect": "skipped stitch",
                                            "top.1.count": "2", "top.1.machine": "12"}
    assert t.get_audit_status()["facts"] == {"audit.status": "no audit run"}
    assert t.get_checklist("skipped_stitch")["checks"]
    assert t.get_ai_vs_checker()["facts"]["ai.agreed"] == "3"  # the good seam was agreed too


class ScriptedSpark:
    """Turn 1 calls get_dhu; later turns return the queued templates in order."""

    def __init__(self, templates):
        self.templates, self.turn = list(templates), 0
        self.chat = self.completions = self

    def create(self, messages, **kw):
        self.turn += 1
        if self.turn == 1:
            call = type("Call", (), dict(id="c1", function=type("F", (), dict(name="get_dhu", arguments=json.dumps({"scope": "session"})))()))()
            msg = type("M", (), dict(content=None, tool_calls=[call], model_dump=lambda self, **k: {"role": "assistant", "content": None}))()
        else:
            msg = type("M", (), dict(content=self.templates.pop(0), tool_calls=None))()
        usage = type("U", (), dict(prompt_tokens=100, completion_tokens=50))()
        return type("R", (), dict(choices=[type("C", (), dict(message=msg))()], usage=usage))()


def test_agent_fills_numbers_from_tools(session):
    con, s = session
    r = run_report(ScriptedSpark(["## Buyer summary\nDHU {{dhu.session.dhu}} on {{dhu.session.garments}} garments."]), con, s)
    assert r["validator_ok"] and r["body_md"].endswith("DHU 50.0 on 4 garments.")
    assert r["trace"]["tool_calls"] == [{"name": "get_dhu", "arguments": {"scope": "session"}}]


def test_invented_number_gets_one_retry_then_is_flagged(session):
    con, s = session
    fixed = run_report(ScriptedSpark(["DHU was 50 today.", "DHU {{dhu.session.dhu}} today."]), con, s)
    assert fixed["validator_ok"] and fixed["body_md"] == "DHU 50.0 today."
    still_bad = run_report(ScriptedSpark(["DHU was 50 today.", "DHU was 50.0 today."]), con, s)
    assert not still_bad["validator_ok"] and any("number typed" in e for e in still_bad["errors"])
