"""Ingest the tailor's phone photos into a labelled manifest.

Capture protocol given to the tailor: 3 shirting cottons x 7 types. Per fabric: 4 good pairs, 3 pairs each of
skipped and broken, 2 pairs of every other defect type (18 pairs); 4 photos per pair at chalk marks 15 cm apart. Photos arrive on WhatsApp as Documents, each group
preceded by a text tag "<fabric>-<type>", e.g. "B-3". Within a tag, photos are in pair order:
photo i -> pair i // 4 + 1, chalk mark i % 4 + 1.

Reference pairs are tagged "<fabric>-<type>-REF" (1 good and 1 puckering pair per fabric, e.g. "A-1-REF", "A-5-REF").
They are separate sewn pairs used for few-shot references and seam-threshold calibration, never for test metrics,
so they go to their own folder and manifest: <out>/ref/.

Inputs (either):
  WhatsApp "Export chat" with media, unzipped or as .zip (iOS `_chat.txt` or Android `WhatsApp Chat with *.txt`)
  --folders: a directory with one sub-folder per tag (B-3/...), photos sorted by capture time then name

python -m stitchcheck.phone.ingest <export.zip | export_dir> [--folders] [--out data/phone]

Writes <out>/images/<photo_id>.jpg (EXIF rotation applied, all metadata incl. GPS stripped) and <out>/manifest.csv,
and the same under <out>/ref/ for reference pairs.
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
PAIRS_PER_TYPE = {1: 4, 2: 3, 3: 3}  # good 4, skipped and broken 3, every other type 2
# Skipped stitch and puckering run along the whole seam, so a given chalk mark may not show them.
# The other defects are made at every chalk mark.
WHOLE_SEAM_TYPES = {2, 5}
PHOTOS_PER_PAIR = 4
REF_TYPES = {1: 1, 5: 1}  # reference pairs per fabric: 1 good, 1 puckering
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp"}

TAG = re.compile(r"(?<![A-Za-z0-9])([ABCabc])\s*[-–]\s*([1-7])(?:\s*[-–]\s*(REF))?(?![0-9A-Za-z]|\s*[-–]\s*REF)", re.IGNORECASE)
# iOS:     [28/10/26, 7:12:03 PM] Name: text        attachment: <attached: 00000012-IMG_1234.jpg>
# Android: 28/10/26, 7:12 pm - Name: text           attachment: IMG_1234.jpg (file attached)
MSG_START = re.compile(r"^\[?\d{1,4}[/.-]\d{1,2}[/.-]\d{1,4},? \d{1,2}:\d{2}")
IOS_ATTACH = re.compile(r"<attached:\s*([^>]+)>")
ANDROID_ATTACH = re.compile(r"^(?:.*?:\s)?(\S.*?\.\w{2,5}) \(file attached\)", re.MULTILINE)


def expected_count(type_id, ref=False):
    if ref:
        return REF_TYPES.get(type_id, 0) * PHOTOS_PER_PAIR
    return PAIRS_PER_TYPE.get(type_id, 2) * PHOTOS_PER_PAIR


@dataclass
class Photo:
    tag: str
    source: Path
    sent_at: str = ""


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


def _normalise_tag(match):
    return f"{match.group(1).upper()}-{match.group(2)}" + ("-REF" if match.group(3) else "")


def parse_chat(chat_text, media_dir):
    """Walk messages in order; a tag sets the label for every following attachment until the next tag."""
    photos, warnings, current = [], [], None
    for msg in _messages(chat_text):
        body = msg.split(": ", 1)[1] if ": " in msg.split("\n", 1)[0] else msg
        files = IOS_ATTACH.findall(msg) + ANDROID_ATTACH.findall(body)
        text = IOS_ATTACH.sub("", ANDROID_ATTACH.sub("", body))
        tags = TAG.findall(text)
        if tags:
            current = _normalise_tag(TAG.search(text))
            if len(set(tags)) > 1:
                warnings.append(f"several tags in one message, using {current}: {msg[:80]!r}")
        if re.search(r"\b(media|image|document) omitted", body, re.IGNORECASE):
            warnings.append(f"media missing (export was made without media?): {msg[:80]!r}")
        for name in files:
            path = media_dir / name.strip()
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if current is None:
                warnings.append(f"photo before any tag, skipped: {name}")
            elif not path.exists():
                warnings.append(f"attachment not found in export: {name}")
            else:
                photos.append(Photo(current, path, MSG_START.match(msg).group(0).strip("[") if MSG_START.match(msg) else ""))
    return photos, warnings


def _capture_time(path):
    try:
        with Image.open(path) as im:
            return im.getexif().get(306, "") or ""  # DateTime
    except OSError:
        return ""


def parse_folders(root):
    photos, warnings = [], []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        m = TAG.fullmatch(d.name.strip())
        if not m:
            warnings.append(f"folder name is not a tag, skipped: {d.name}")
            continue
        files = sorted((f for f in d.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES),
                       key=lambda f: (_capture_time(f), f.name))
        photos += [Photo(_normalise_tag(m), f) for f in files]
    return photos, warnings


def label_photos(photos):
    """Assign pair / chalk-mark position by order within each tag; drop exact re-sends."""
    rows, warnings, seen = [], [], {}
    by_tag = {}
    for p in photos:
        digest = hashlib.md5(p.source.read_bytes()).hexdigest()
        if digest in seen:
            warnings.append(f"duplicate file {p.source.name} (same as {seen[digest]}), skipped")
            continue
        seen[digest] = p.source.name
        by_tag.setdefault(p.tag, []).append((p, digest))
    for tag in sorted(by_tag):
        fabric, type_id, ref = tag[0], int(tag[2]), tag.endswith("-REF")
        items = by_tag[tag]
        expected = expected_count(type_id, ref)
        if ref and type_id not in REF_TYPES:
            warnings.append(f"{tag}: reference pairs are expected only for types {sorted(REF_TYPES)}")
        if len(items) != expected:
            warnings.append(f"{tag}: {len(items)} photos, expected {expected}; pair numbers may be off")
        for i, (p, digest) in enumerate(items):
            pair, mark = i // PHOTOS_PER_PAIR + 1, i % PHOTOS_PER_PAIR + 1
            pair_id = f"{fabric}{type_id}" + ("-REF" if ref else "") + f"-p{pair}"
            rows.append(dict(
                photo_id=f"{pair_id}-m{mark}", tag=tag, ref=int(ref), fabric=fabric, fabric_desc=FABRICS[fabric],
                type_id=type_id, label=TYPES[type_id], unseen=int(type_id == 7),
                check_visible=int(type_id in WHOLE_SEAM_TYPES),
                pair_id=pair_id, chalk_mark=mark, order_in_tag=i + 1,
                count_ok=int(len(items) == expected), source_file=p.source.name, sent_at=p.sent_at, md5=digest))
    missing = [f"{f}-{t}" for f in FABRICS for t in TYPES if f"{f}-{t}" not in by_tag]
    missing += [f"{f}-{t}-REF" for f in FABRICS for t in REF_TYPES if f"{f}-{t}-REF" not in by_tag]
    if missing:
        warnings.append(f"tags with no photos: {', '.join(missing)}")
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


def ingest(src, out, folders=False):
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
        df, more = label_photos(photos)
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
    ap.add_argument("--out", type=Path, default=Path("data/phone"))
    args = ap.parse_args()
    df, warnings = ingest(args.src, args.out, args.folders)
    for w in warnings:
        print("WARN", w)
    if df.empty:
        sys.exit("no photos ingested")
    for ref, name in ((0, "test"), (1, "ref")):
        part = df[df["ref"] == ref]
        if len(part):
            print(f"{name}: {len(part)} photos -> {args.out / ('ref' if ref else '') / 'manifest.csv'}")
            print(pd.crosstab(part["label"], part["fabric"], margins=True).to_string())


if __name__ == "__main__":
    main()
