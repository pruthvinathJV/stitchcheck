"""Score tech-pack extractions against hand-labelled ground truth, as preregistered in docs/techpack_eval.md.

python scripts/techpack/score.py --truth data/techpack/ground_truth_seen.json [--truth data/techpack/ground_truth_unseen.json]

Groups: seen = TP-01 to TP-05 (ground truth read before the prompt was written), unseen = TP-06 and TP-07 (headline).
Writes results/techpack/score_fields.csv, score_sheets.csv and score_summary.json.
"""
import argparse
import json
import math
import re
from pathlib import Path

import pandas as pd

from stitchcheck.techpack.extract import _level, map_part, norm, validate

EXTRACTIONS, OUT = Path("results/techpack/extractions"), Path("results/techpack")
SEEN = {"TP-01", "TP-02", "TP-03", "TP-04", "TP-05"}
HEADER = {"buyer": "buyer", "style_no": "style_no", "style_name": "style_name", "fabric": "fabric", "color": "color", "thread": "thread"}
OP_FIELDS = ["stitch_type", "spi_target", "spi_tolerance", "seam_allowance_mm", "critical"]


def gt_part(part):
    """Ground-truth part keys use app-style names (e.g. collar_band); map them with the same synonym table."""
    return map_part(part.replace("_", " "))


def same(truth, got):
    """Exact after normalisation; numbers compared as numbers; null must be null."""
    if truth is None:
        return got is None
    if got is None:
        return False
    if isinstance(truth, bool) or isinstance(got, bool):
        return truth is got
    if isinstance(truth, (int, float)) and not isinstance(truth, bool):
        try:
            return math.isclose(float(truth), float(got))
        except (TypeError, ValueError):
            return False
    return norm(truth) == norm(got)


def token_f1(a, b):
    ta, tb = set(norm(a).split()), set(norm(b).split())
    if not ta or not tb:
        return 0.0
    p, r = len(ta & tb) / len(tb), len(ta & tb) / len(ta)
    return 2 * p * r / (p + r) if p + r else 0.0


def match_operations(gt_ops, ops):
    """Pair each ground-truth row with an extracted row: same app part (via the synonym table), then the most
    similar operation text. Unpaired extracted rows are hallucinated rows."""
    free, pairs = list(range(len(ops))), []
    for g in gt_ops:
        cands = [i for i in free if ops[i].get("app_part") == gt_part(g["part"])]
        best = max(cands, key=lambda i: token_f1(g["operation"], ops[i]["operation"]), default=None)
        if best is not None:
            free.remove(best)
        pairs.append((g, ops[best] if best is not None else None))
    return pairs, [ops[i] for i in free]


def severity_set(text):
    return {s.strip() for s in str(text).split("/")}


def score_sheet(gt, ext):
    plan = ext["plan"]
    v = validate(plan)  # also sets app_part on every operation
    rows, halluc = [], []
    for k, gk in HEADER.items():
        got = plan["header"][k]["value"]
        rows.append(dict(field=f"header.{k}", truth=gt.get(gk), got=got, correct=same(gt.get(gk), got),
                         lenient=gt.get(gk) is not None and got is not None and token_f1(gt[gk], got) >= 0.8))
        if gt.get(gk) is None and got is not None:
            halluc.append(f"header.{k}={got!r} where the sheet gives none")
    for k in ("inspection_level", "critical", "major", "minor"):
        got = plan["aql"][k]["value"]
        truth = gt["aql"][k]
        ok = same(truth, got) if k != "inspection_level" else got is not None and _level(got) == str(truth).upper()
        rows.append(dict(field=f"aql.{k}", truth=truth, got=got, correct=ok, lenient=ok))
    pairs, extra = match_operations(gt["operations"], plan["operations"])
    for g, op in pairs:
        name = f"op.{g['part']}"
        rows.append(dict(field=f"{name}.found", truth=True, got=op is not None, correct=op is not None, lenient=op is not None))
        for f in OP_FIELDS:
            got = op[f] if op else None
            rows.append(dict(field=f"{name}.{f}", truth=g[f], got=got, correct=op is not None and same(g[f], got),
                             lenient=op is not None and same(g[f], got)))
    halluc += [f"operation row not on the sheet: {op['part_as_written']!r} {op['operation']!r}" for op in extra]
    rules, matched = list(plan["severity_rules"]), 0
    for g in gt["severity_rules"]:
        hit = next((r for r in rules if r["defect"] == g["defect"] and r["severity"] in severity_set(g["severity"])), None)
        if hit:
            rules.remove(hit)
            matched += 1
    halluc += [f"severity rule not on the sheet: {r['text_as_written']!r}" for r in rules]
    gt_missing = [m.split(" ")[0] for m in gt.get("missing_or_unreadable", [])]
    missing_flagged = sum(any(m in x for x in v["missing"]) for m in gt_missing)
    gt_unsup = [u.split(" ")[0] for u in gt.get("unsupported_by_app", [])]
    unsup_flagged = sum(any(u in x for x in v["unsupported"]) for u in gt_unsup)
    hand_truth = gt.get("handwritten_corrections", [])
    df = pd.DataFrame(rows)
    return df, dict(fields=len(df), correct=int(df["correct"].sum()), lenient=int(df["lenient"].sum()),
                    rules=len(gt["severity_rules"]), rules_matched=matched, hallucinations=len(halluc),
                    hallucination_items=halluc, missing=len(gt_missing), missing_flagged=missing_flagged,
                    unsupported=len(gt_unsup), unsupported_flagged=unsup_flagged,
                    handwritten=len(hand_truth), handwritten_listed=min(len(hand_truth), len(plan["handwritten_changes"])),
                    unmapped_parts=v["unmapped_parts"])


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", nargs="+", required=True)
    args = ap.parse_args()
    truth = {g["sheet_id"]: g for f in args.truth for g in json.loads(Path(f).read_text())}
    field_rows, sheets = [], []
    for sheet, gt in sorted(truth.items()):
        path = EXTRACTIONS / f"{sheet}.json"
        if not path.exists():
            print(f"{sheet}: no extraction")
            continue
        df, s = score_sheet(gt, json.loads(path.read_text()))
        group = "seen" if sheet in SEEN else "unseen"
        field_rows.append(df.assign(sheet=sheet, group=group))
        sheets.append(dict(sheet=sheet, group=group, **s))
    fields, sheets = pd.concat(field_rows), pd.DataFrame(sheets)
    fields.to_csv(OUT / "score_fields.csv", index=False)
    sheets.drop(columns=["hallucination_items"]).to_csv(OUT / "score_sheets.csv", index=False)
    summary = {}
    for group, g in sheets.groupby("group"):
        k, n = int(g["correct"].sum()), int(g["fields"].sum())
        summary[group] = dict(sheets=list(g["sheet"]), field_accuracy=round(k / n, 3), field_accuracy_ci=wilson(k, n),
                              fields=n, lenient_accuracy=round(int(g["lenient"].sum()) / n, 3),
                              rule_recall=f"{int(g['rules_matched'].sum())}/{int(g['rules'].sum())}",
                              hallucinations=int(g["hallucinations"].sum()),
                              hallucination_items=[x for items in g["hallucination_items"] for x in items],
                              missing_flagged=f"{int(g['missing_flagged'].sum())}/{int(g['missing'].sum())}",
                              unsupported_flagged=f"{int(g['unsupported_flagged'].sum())}/{int(g['unsupported'].sum())}",
                              handwritten_listed=f"{int(g['handwritten_listed'].sum())}/{int(g['handwritten'].sum())}")
    (OUT / "score_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
