import csv

import pandas as pd
import pytest
from PIL import Image

from stitchcheck.phone import ingest, review


def _img(path, seed, exif_rotate=False):
    im = Image.new("RGB", (40, 20), (seed % 256, (seed * 7) % 256, (seed * 13) % 256))
    exif = Image.Exif()
    exif[0x0112] = 6 if exif_rotate else 1  # orientation
    exif[0x8825] = {2: (12.0, 0.0, 0.0)}    # GPS block that must be stripped
    im.save(path, exif=exif)


def _ios_export(tmp_path, tags=None):
    lines, n = [], 0
    for tag in tags or ingest.EXPECTED_TAGS:
        lines.append(f"[28/10/26, 7:12:03 PM] Ravi Tailor: {tag}")
        for _ in range(4):
            name = f"{n:08d}-IMG_{n}.jpg"
            _img(tmp_path / name, n, exif_rotate=(n == 0))
            lines.append(f"‎[28/10/26, 7:12:05 PM] Ravi Tailor: ‎<attached: {name}>")
            n += 1
    (tmp_path / "_chat.txt").write_text("\n".join(lines))
    return n


def test_spec_counts():
    assert len(ingest.TEST_TAGS) == 54 and len(ingest.REF_TAGS) == 6
    assert ingest.TEST_TAGS[:5] == ["A-1-1", "A-1-2", "A-1-3", "A-1-4", "A-2-1"]


def test_full_ios_export(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    assert _ios_export(src) == 240
    df, warnings = ingest.ingest(src, out)
    assert len(df) == 240 and warnings == []

    test = pd.read_csv(out / "manifest.csv")
    assert len(test) == 216 and test["pair_id"].nunique() == 54 and test["ref"].eq(0).all()
    assert test.groupby("label").size().to_dict() == {
        "good": 48, "skipped_stitch": 36, "broken_stitch": 36, "crooked_seam": 24,
        "puckering": 24, "stain_damage": 24, "open_seam": 24}
    b32 = test[test["tag"] == "B-3-2"]
    assert list(b32["photo_id"]) == ["B3-p2-m1", "B3-p2-m2", "B3-p2-m3", "B3-p2-m4"]
    assert list(b32["chalk_cm"]) == [10, 25, 40, 55]
    assert set(test[test["type_id"] == 7]["unseen"]) == {1}
    assert set(test[test["check_visible"] == 1]["label"]) == {"skipped_stitch", "puckering"}

    ref = pd.read_csv(out / "ref" / "manifest.csv")
    assert len(ref) == 24 and ref["ref"].eq(1).all()
    assert ref.groupby("label").size().to_dict() == {"good": 12, "puckering": 12}
    assert sorted(ref["pair_id"].unique())[:2] == ["A1-REF", "A5-REF"]
    assert not set(ref["photo_id"]) & {p.stem for p in (out / "images").iterdir()}

    first = Image.open(out / "images" / "A1-p1-m1.jpg")
    assert first.size == (20, 40)               # EXIF rotation applied
    assert 0x8825 not in first.getexif()        # GPS stripped


def test_single_pair_pilot(tmp_path):
    src, out = tmp_path / "src", tmp_path / "pilot"
    src.mkdir()
    _ios_export(src, tags=["A-1-1"])
    df, warnings = ingest.ingest(src, out, pairs=["a-1-1"])
    assert list(df["photo_id"]) == ["A1-p1-m1", "A1-p1-m2", "A1-p1-m3", "A1-p1-m4"] and warnings == []
    assert len(pd.read_csv(out / "manifest.csv")) == 4
    _, warnings = ingest.ingest(src, tmp_path / "all")  # without --pairs, the other 59 tags are reported missing
    assert any("59 expected tags with no photos" in w for w in warnings)
    with pytest.raises(ValueError):
        ingest.ingest(src, out, pairs=["A-1-5"])   # good has only 4 pairs


def test_android_warnings(tmp_path):
    names = [f"IMG-2026102{i}-WA00{i:02d}.jpg" for i in range(12)]
    for i, n in enumerate(names):
        _img(tmp_path / n, i)
    att = lambda i: f"28/10/26, 7:{10 + i} pm - Ravi: {names[i]} (file attached)"
    chat = "\n".join([
        att(0), "C-6-1",                           # caption tag on the first photo of the pair
        att(1), att(2), att(3), att(3),            # exact re-send of photo 3 is dropped
        "28/10/26, 7:30 pm - Ravi: A-2",           # old type-level tag: unknown
        att(4),
        "28/10/26, 7:31 pm - Ravi: A-5-REFX",      # typo: unknown, never falls back to A-5
        att(5),
        "28/10/26, 7:32 pm - Ravi: B-3-2",         # first attempt, 2 photos
        att(6), att(7),
        "28/10/26, 7:33 pm - Ravi: B-3-2",         # re-sent: the latest wins
        att(8), att(9), att(10),
        "28/10/26, 7:34 pm - Ravi: <Media omitted>",
    ])
    (tmp_path / "WhatsApp Chat with Ravi.txt").write_text(chat)
    df, warnings = ingest.ingest(tmp_path, tmp_path / "out")
    assert df.groupby("tag")["source_file"].apply(list).to_dict() == {
        "C-6-1": names[0:4], "B-3-2": names[8:11]}
    text = "\n".join(warnings)
    for expected in ["duplicate file", "unknown tag 'A-2'", "unknown tag 'A-5-REFX'", "photo without a valid tag",
                     "B-3-2: tag sent 2 times", "B-3-2: 3 photos, expected 4", "media missing",
                     "58 expected tags with no photos"]:
        assert expected in text, expected


def test_folders_mode(tmp_path):
    d = tmp_path / "B-5-2"
    d.mkdir()
    for i in range(4):
        _img(d / f"{i}.jpg", i)
    (tmp_path / "notes").mkdir()
    (tmp_path / "B-5").mkdir()
    df, warnings = ingest.ingest(tmp_path, tmp_path / "out", folders=True, pairs=["B-5-2"])
    assert list(df["pair_id"].unique()) == ["B5-p2"] and set(df["label"]) == {"puckering"}
    assert sum("not a known tag" in w for w in warnings) == 2


def test_review_export_keeps_accepted_with_reviewed_label(tmp_path):
    src, out = tmp_path / "src", tmp_path / "phone"
    src.mkdir()
    _ios_export(src)
    ingest.ingest(src, out)
    review.save_reviews(out / "review.csv", {
        "A1-p1-m1": dict(photo_id="A1-p1-m1", status="accepted", reason="", label=""),
        "A2-p1-m2": dict(photo_id="A2-p1-m2", status="accepted", reason="", label="good"),
        "A3-p1-m3": dict(photo_id="A3-p1-m3", status="rejected", reason="blurry", label=""),
    })
    review.export(out, tmp_path / "labels.csv")
    rows = list(csv.DictReader((tmp_path / "labels.csv").open()))
    assert [(r["photo_id"], r["label_reviewed"]) for r in rows] == [("A1-p1-m1", "good"), ("A2-p1-m2", "good")]
