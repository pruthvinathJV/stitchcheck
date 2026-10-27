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


def _tags():
    return [f"{f}-{t}" for f in "ABC" for t in range(1, 8)] + [f"{f}-{t}-REF" for f in "ABC" for t in (1, 5)]


def _ios_export(tmp_path):
    lines, n = [], 0
    for tag in _tags():
        lines.append(f"[28/10/26, 7:12:03 PM] Ravi Tailor: {tag}")
        for _ in range(ingest.expected_count(int(tag[2]), tag.endswith("REF"))):
            name = f"{n:08d}-IMG_{n}.jpg"
            _img(tmp_path / name, n, exif_rotate=(n == 0))
            lines.append(f"‎[28/10/26, 7:12:05 PM] Ravi Tailor: ‎<attached: {name}>")
            n += 1
    (tmp_path / "_chat.txt").write_text("\n".join(lines))
    return n


def test_ios_export_maps_tag_order_to_labels(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    src.mkdir()
    n = _ios_export(src)
    df, warnings = ingest.ingest(src, out)
    assert n == 240 and len(df) == 240 and warnings == []
    test = pd.read_csv(out / "manifest.csv")
    assert len(test) == 216 and test["ref"].eq(0).all()
    assert test.groupby("label").size().to_dict() == {
        "good": 48, "skipped_stitch": 36, "broken_stitch": 36, "crooked_seam": 24,
        "puckering": 24, "stain_damage": 24, "open_seam": 24}
    ref = pd.read_csv(out / "ref" / "manifest.csv")
    assert len(ref) == 24 and ref["ref"].eq(1).all()
    assert ref.groupby("label").size().to_dict() == {"good": 12, "puckering": 12}
    assert sorted(ref["pair_id"].unique())[:2] == ["A1-REF-p1", "A5-REF-p1"]
    assert (out / "ref" / "images" / "C5-REF-p1-m4.jpg").exists()
    assert not set(ref["photo_id"]) & {p.stem for p in (out / "images").iterdir()}
    b3 = df[df["tag"] == "B-3"].sort_values("order_in_tag")
    assert list(b3["photo_id"])[:5] == ["B3-p1-m1", "B3-p1-m2", "B3-p1-m3", "B3-p1-m4", "B3-p2-m1"]
    assert set(df[df["type_id"] == 7]["unseen"]) == {1}
    assert set(df[df["check_visible"] == 1]["label"]) == {"skipped_stitch", "puckering"}
    first = Image.open(out / "images" / "A1-p1-m1.jpg")
    assert first.size == (20, 40)               # EXIF rotation applied
    assert 0x8825 not in first.getexif()        # GPS stripped


def test_android_export_with_caption_tag_and_resend(tmp_path):
    for i in range(3):
        _img(tmp_path / f"IMG-2026102{i}-WA000{i}.jpg", i)
    chat = "\n".join([
        "28/10/26, 7:12 pm - Ravi: IMG-20261020-WA0000.jpg (file attached)",
        "C-6",
        "28/10/26, 7:13 pm - Ravi: IMG-20261021-WA0001.jpg (file attached)",
        "28/10/26, 7:14 pm - Ravi: IMG-20261021-WA0001.jpg (file attached)",
        "28/10/26, 7:15 pm - Ravi: a-2",
        "28/10/26, 7:16 pm - Ravi: IMG-20261022-WA0002.jpg (file attached)",
        "28/10/26, 7:17 pm - Ravi: <Media omitted>",
    ])
    (tmp_path / "WhatsApp Chat with Ravi.txt").write_text(chat)
    df, warnings = ingest.ingest(tmp_path, tmp_path / "out")
    # "C-6" is a caption line inside the first message, so it tags that photo and the next. The re-send is dropped.
    assert sorted(df["source_file"]) == ["IMG-20261020-WA0000.jpg", "IMG-20261021-WA0001.jpg", "IMG-20261022-WA0002.jpg"]
    assert list(df["tag"]) == ["A-2", "C-6", "C-6"]
    assert any("duplicate" in w for w in warnings)
    assert any("media missing" in w for w in warnings)
    assert any("expected 8" in w for w in warnings)


def test_folders_mode(tmp_path):
    d = tmp_path / "B-5"
    d.mkdir()
    for i in range(8):
        _img(d / f"{i}.jpg", i)
    (tmp_path / "notes").mkdir()
    df, warnings = ingest.ingest(tmp_path, tmp_path / "out", folders=True)
    assert list(df["pair_id"].unique()) == ["B5-p1", "B5-p2"] and set(df["label"]) == {"puckering"}
    assert any("not a tag" in w for w in warnings)


def test_review_export_keeps_accepted_with_reviewed_label(tmp_path):
    src, out = tmp_path / "src", tmp_path / "phone"
    src.mkdir()
    _ios_export(src)
    ingest.ingest(src, out)
    review.save_reviews(out / "review.csv", {
        "A1-p1-m1": dict(photo_id="A1-p1-m1", status="accepted", reason="", label=""),
        "A3-p1-m2": dict(photo_id="A3-p1-m2", status="accepted", reason="", label="good"),
        "A3-p1-m3": dict(photo_id="A3-p1-m3", status="rejected", reason="blurry", label=""),
    })
    review.export(out, tmp_path / "labels.csv")
    rows = list(csv.DictReader((tmp_path / "labels.csv").open()))
    assert [(r["photo_id"], r["label_reviewed"]) for r in rows] == [("A1-p1-m1", "good"), ("A3-p1-m2", "good")]
