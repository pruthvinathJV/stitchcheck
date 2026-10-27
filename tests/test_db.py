import sqlite3

import pytest

from stitchcheck.backend.db import completed_garments, connect, insert, session_inspections, update
from stitchcheck.qc.counts import dhu


@pytest.fixture
def con(tmp_path):
    return connect(tmp_path / "t.db")


def seed(con):
    s = insert(con, "session", date="2026-10-28", style="men's shirt", fabric="A", lot_size=2400)
    insert(con, "line_plan", session_id=s, part="collar", sequence_no=1, operator_id="OP01", machine_id="M01")
    b = insert(con, "bundle", session_id=s, bundle_no=34)
    gs = [insert(con, "garment", bundle_id=b, garment_no=n) for n in (1, 2)]
    return s, gs


def test_round_trip_with_json_columns(con):
    s, (g1, _) = seed(con)
    i = insert(con, "inspection", garment_id=g1, reason="critical", photo_path="p.jpg", seam_found=1,
               classifier_top2=[["broken_stitch", 0.9], ["good", 0.05]], config_hash="abc")
    row = session_inspections(con, s)[0]
    assert row["id"] == i and row["classifier_top2"][0] == ["broken_stitch", 0.9] and row["bundle_no"] == 34
    assert row["decision"] == "pending"
    assert con.execute("SELECT critical_parts FROM session").fetchone()["critical_parts"] == ["collar", "placket"]


def test_constraints(con):
    s, (g1, _) = seed(con)
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, "inspection", garment_id=g1, reason="critical", photo_path="p", seam_found=1, config_hash="x",
               decision="reject")  # split into not_a_defect / retake
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, "line_plan", session_id=s, part="collar", sequence_no=2, operator_id="OP09", machine_id="M09")
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, "garment", bundle_id=999, garment_no=1)  # foreign key


def test_dhu_from_the_database(con):
    s, (g1, g2) = seed(con)
    i = insert(con, "inspection", garment_id=g1, reason="critical", photo_path="p", seam_found=1, config_hash="x")
    update(con, "inspection", i, decision="agree", final_label="broken_stitch", final_part="collar",
           operator_id="OP01", machine_id="M01")
    for g in (g1, g2):
        update(con, "garment", g, status="complete", completed_at="2026-10-28T10:00:00")
    assert completed_garments(con, s) == [g1, g2]
    assert dhu(session_inspections(con, s), completed_garments(con, s)) == 50.0
