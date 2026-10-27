"""Ingest the tailor's phone photos into a labelled manifest.

Capture protocol (final): 3 shirting cottons (A white, B dark, C checks) x 7 seam types. Per fabric: 5 good pairs,
3 skipped, 3 broken, 3 puckering, 2 each of crooked, stain and open seam = 20 pairs; 60 pairs, 240 photos.
Each pair is announced by one WhatsApp text tag "<fabric>-<type>-<pair>" (e.g. "B-3-2"), followed by exactly
4 photos sent as Documents, in chalk-mark order (10, 25, 40, 55 cm along the seam).

The last good pair and the last puckering pair of each fabric ("<fabric>-1-5", "<fabric>-5-3") are reference
pairs: 6 pairs, 24 photos. They are used for few-shot references and seam-threshold calibration, never for test
metrics, so they go to their own folder and manifest: <out>/ref/. The other 54 pairs (216 photos) are the test set.

Inputs (either):
  WhatsApp "Export chat" with media, unzipped or as .zip (iOS `_chat.txt` or Android `WhatsApp Chat with *.txt`)
  --folders: a directory with one sub-folder per tag (B-3-2/...), photos sorted by capture time then name

python -m stitchcheck.phone.ingest <export.zip | export_dir> [--folders] [--out data/phone] [--pairs A-1-1 ...]

--pairs ingests only those tags (e.g. the first quality-check pair) into data/phone/pilot unless --out is given,
and checks completeness against that list only.

Writes <out>/images/<photo_id>.jpg (EXIF rotation applied, all metadata incl. GPS stripped) and <out>/manifest.csv,
and the same under <out>/ref/ for reference pairs. Warns on: tags without exactly 4 photos, duplicate tags
(the latest one is kept), unknown tags (their photos are skipped) and expected tags that never arrived.
"""
import argparse
import hashlib
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from PIL import Image, ImageOps

FABRICS = {"A": "white", "B": "dark", "C": "checks"}
TYPES = {1: "good", 2: "skipped_stitch", 3: "broken_stitch", 4: "crooked_seam",
         5: "puckering", 6: "stain_damage", 7: "open_seam"}  # 7 is the unseen-defect probe
PAIRS_PER_TYPE = {1: 5, 2: 3, 3: 3, 4: 2, 5: 3, 6: 2, 7: 2}
REF_PAIR = {1: 5, 5: 3}  # per fabric, these pairs of good and puckering are references, never test
# Skipped stitch and puckering run along the whole seam, so a given chalk mark may not show them.
# The other defects are made at every chalk mark.
WHOLE_SEAM_TYPES = {2, 5}
CHALK_MARKS_CM = (10, 25, 40, 55)
PHOTOS_PER_PAIR = len(CHALK_MARKS_CM)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


EXPECTED_TAGS = [f"{f}-{t}-{p}" for f in FABRICS for t in TYPES for p in range(1, PAIRS_PER_TYPE[t] + 1)]
REF_TAGS = [t for t in EXPECTED_TAGS if REF_PAIR.get(int(t.split("-")[1])) == int(t.split("-")[2])]
TEST_TAGS = [t for t in EXPECTED_TAGS if t not in REF_TAGS]

# Anything that looks like a tag (letter-number[-suffix]); checked against EXPECTED_TAGS afterwards,
# so typos like "A-1" or "A-1-REF" are reported as unknown rather than silently matched.
TAG_LIKE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*[-–]\s*(\d{1,2})(?:\s*[-–]\s*([A-Za-z0-9]+))?(?![A-Za-z0-9])")
# iOS:     [28/10/26, 7:12:03 PM] Name: text        attachment: <attached: 00000012-IMG_1234.jpg>
# Android: 28/10/26, 7:12 pm - Name: text           attachment: IMG_1234.jpg (file attached)
MSG_START = re.compile(r"^\[?\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4},? \d{1,2}:\d{2}")
IOS_ATTACH = re.compile(r"<attached:\s*([^>]+)>")
ANDROID_ATTACH = re.compile(r"^(?:.*?:\s)?(\S.*?\.\w{2,5}) \(file attached\)", re.MULTILINE)


@dataclass
class Photo:
    tag: str
    occurrence: int  # which appearance of the tag this photo follows (to resolve duplicate tags)
    source: Path
    sent_at: str = ""


def normalise_tag(match):
    letter, number, suffix = match.groups()
    return f"{letter.upper()}-{int(number)}" + (f"-{suffix.upper()}" if suffix else "")


def _messages(chat_text):
    """Split a chat export into messages (a message may span several lines)."""
    chat_text = chat_text.replace("‎", "").replace(" ", " ")
    msgs = []
    for line in chat_text.splitlines():
        if MSG_START.match(line) or not msgs:
            msgs.append(line)
        else:
            msgs[-1] += "\n" + line
    return msgs


def parse_chat(chat_text, media_dir):
    """Walk messages in order; a valid tag applies to every following attachment until the next tag."""
    photos, warnings, current, occurrence = [], [], None, 0
    for msg in _messages(chat_text):
        body = msg.split(": ", 1)[1] if ": " in msg.split("\n", 1)[0] else msg
        files = IOS_ATTACH.findall(msg) + ANDROID_ATTACH.findall(body)
        text = IOS_ATTACH.sub("", ANDROID_ATTACH.sub("", body))
        tags = [normalise_tag(m) for m in TAG_LIKE.finditer(text)]
        if tags:
            if len(set(tags)) > 1:
                warnings.append(f"several tags in one message, using {tags[0]}: {msg[:80]!r}")
            occurrence += 1
            if tags[0] in EXPECTED_TAGS:
                current = tags[0]
            else:
                current = None
                warnings.append(f"unknown tag {tags[0]!r}; photos after it are skipped until the next valid tag")
        if re.search(r"\b(media|image|document) omitted", body, re.IGNORECASE):
            warnings.append(f"media missing (export was made without media?): {msg[:80]!r}")
        for name in files:
            path = media_dir / name.strip()
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if current is None:
                warnings.append(f"photo without a valid tag, skipped: {name}")
            elif not path.exists():
                warnings.append(f"attachment not found in export: {name}")
            else:
                start = MSG_START.match(msg)
                photos.append(Photo(current, occurrence, path, start.group(0).strip("[") if start else ""))
    return photos, warnings


def _capture_time(path):
    try:
        with Image.open(path) as im:
            return im.getexif().get(306, "") or ""  # DateTime
    except OSError:
        return ""


def parse_folders(root):
    photos, warnings = [], []
    for i, d in enumerate(sorted(p for p in root.iterdir() if p.is_dir())):
        m = TAG_LIKE.fullmatch(d.name.strip())
        tag = normalise_tag(m) if m else None
        if tag not in EXPECTED_TAGS:
            warnings.append(f"folder name is not a known tag, skipped: {d.name}")
            continue
        files = sorted((f for f in d.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES),
                       key=lambda f: (_capture_time(f), f.name))
        photos += [Photo(tag, i, f) for f in files]
    return photos, warnings


def label_photos(photos, expected=EXPECTED_TAGS):
    """One row per photo: pair from the tag, chalk mark from the order within the tag. Drops exact re-sends."""
    rows, warnings, seen = [], [], {}
    by_tag = {}
    for p in photos:
        digest = hashlib.md5(p.source.read_bytes()).hexdigest()
        if digest in seen:
            warnings.append(f"duplicate file {p.source.name} (same as {seen[digest]}), skipped")
            continue
        seen[digest] = p.source.name
        by_tag.setdefault(p.tag, {}).setdefault(p.occurrence, []).append((p, digest))
    for tag in sorted(by_tag, key=EXPECTED_TAGS.index):
        occurrences = by_tag[tag]
        if len(occurrences) > 1:
            warnings.append(f"{tag}: tag sent {len(occurrences)} times; keeping the latest "
                            f"({len(occurrences[max(occurrences)])} photos), dropping the earlier ones")
        items = occurrences[max(occurrences)]
        if len(items) != PHOTOS_PER_PAIR:
            warnings.append(f"{tag}: {len(items)} photos, expected {PHOTOS_PER_PAIR}; chalk marks may be off")
        fabric, type_id, pair = tag.split("-")
        type_id, ref = int(type_id), tag in REF_TAGS
        pair_id = f"{fabric}{type_id}-p{pair}"
        for i, (p, digest) in enumerate(items):
            mark = i + 1
            rows.append(dict(
                photo_id=f"{pair_id}-m{mark}", tag=tag, ref=int(ref), fabric=fabric, fabric_desc=FABRICS[fabric],
                type_id=type_id, label=TYPES[type_id], unseen=int(type_id == 7),
                check_visible=int(type_id in WHOLE_SEAM_TYPES), pair_id=pair_id, chalk_mark=mark,
                chalk_cm=CHALK_MARKS_CM[i] if i < PHOTOS_PER_PAIR else None,
                count_ok=int(len(items) == PHOTOS_PER_PAIR), source_file=p.source.name, sent_at=p.sent_at, md5=digest))
    missing = [t for t in expected if t not in by_tag]
    if missing:
        warnings.append(f"{len(missing)} expected tags with no photos: {', '.join(missing)}")
    return pd.DataFrame(rows), warnings


def export_images(df, sources, out_dir):
    """Copy as clean JPEGs: apply EXIF rotation, strip all metadata (GPS)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = []
    for row, src in zip(df.itertuples(), sources):
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.save(out_dir / f"{row.photo_id}.jpg", quality=95)
            sizes.append(f"{im.width}x{im.height}")
    return sizes


def _find_chat(root):
    chats = sorted(root.rglob("_chat.txt")) + sorted(root.rglob("WhatsApp Chat*.txt"))
    if not chats:
        sys.exit(f"no _chat.txt or 'WhatsApp Chat*.txt' under {root}; export the chat WITH media, or use --folders")
    return chats[0]


def ingest(src, out, folders=False, pairs=None):
    """pairs: optional list of tags to ingest (e.g. ["A-1-1"]); completeness is then checked against it only."""
    expected = EXPECTED_TAGS
    if pairs:
        expected = [normalise_tag(m) if (m := TAG_LIKE.fullmatch(t.strip())) else t for t in pairs]
        bad = [t for t in expected if t not in EXPECTED_TAGS]
        if bad:
            raise ValueError(f"not in the capture spec: {', '.join(bad)}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(src)
        if root.suffix == ".zip":
            zipfile.ZipFile(root).extractall(tmp)
            root = Path(tmp)
        if folders:
            photos, warnings = parse_folders(root)
        else:
            chat = _find_chat(root)
            photos, warnings = parse_chat(chat.read_text(encoding="utf-8", errors="replace"), chat.parent)
        if pairs:
            photos = [p for p in photos if p.tag in expected]
        df, more = label_photos(photos, expected)
        warnings += more
        if df.empty:
            return df, warnings
        by_name = {p.source.name: p.source for p in photos}
        # Test and reference photos never share a folder or manifest.
        for part, folder in ((df[df["ref"] == 0], out), (df[df["ref"] == 1], out / "ref")):
            if part.empty:
                continue
            part = part.assign(size=export_images(part, [by_name[n] for n in part["source_file"]], folder / "images"))
            part.to_csv(folder / "manifest.csv", index=False)
    return df, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--folders", action="store_true")
    ap.add_argument("--pairs", nargs="+", help="ingest only these tags, e.g. A-1-1 (default out: data/phone/pilot)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out = args.out or Path("data/phone/pilot" if args.pairs else "data/phone")
    df, warnings = ingest(args.src, out, args.folders, args.pairs)
    for w in warnings:
        print("WARN", w)
    if df.empty:
        sys.exit("no photos ingested")
    for ref, name in ((0, "test"), (1, "ref")):
        part = df[df["ref"] == ref]
        if len(part):
            print(f"{name}: {len(part)} photos, {part['pair_id'].nunique()} pairs -> "
                  f"{(out / 'ref' if ref else out) / 'manifest.csv'}")
            print(pd.crosstab(part["label"], part["fabric"], margins=True).to_string())
    if args.pairs:
        print(f"review: python -m stitchcheck.phone.review --dir {out}")


if __name__ == "__main__":
    main()
