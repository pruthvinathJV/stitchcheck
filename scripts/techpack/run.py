"""Run the tech-pack extraction once on every sheet (docs/techpack_eval.md).

python scripts/techpack/run.py [--sheets TP-01 TP-07]

Inputs (data/techpack/inputs/): TP-01 and TP-07 as their PDFs; TP-02 to TP-06 as phone photos named
TP-0N_photo.jpg. A sheet whose input is missing is reported and skipped. One call per sheet, no retries except on an
API or schema error (up to 2 more attempts, logged). Writes results/techpack/extractions/<sheet>.json.
"""
import argparse
import json
import time
from pathlib import Path

from stitchcheck.meta_api import spark_client
from stitchcheck.techpack.extract import EFFORT, MAX_SIDE, PROMPT, extract

IN, OUT = Path("data/techpack/inputs"), Path("results/techpack/extractions")
INPUTS = {"TP-01": "TP-01_SB-101.pdf", "TP-07": "TP-07_SB-307.pdf",
          **{f"TP-0{n}": f"TP-0{n}_photo.jpg" for n in range(2, 7)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheets", nargs="+", default=sorted(INPUTS))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    client = spark_client()
    for sheet in args.sheets:
        path = IN / INPUTS[sheet]
        if not path.exists():
            print(f"{sheet}: input {path.name} not found, skipped")
            continue
        errors = []
        for attempt in range(3):
            try:
                r = extract(client, path)
                break
            except Exception as ex:
                errors.append(f"{type(ex).__name__}: {ex}"[:300])
                time.sleep(5)
        else:
            print(f"{sheet}: failed {errors}")
            continue
        r |= dict(sheet=sheet, input=path.name, attempts=len(errors) + 1, attempt_errors=errors, effort=EFFORT,
                  max_side=MAX_SIDE, prompt_chars=len(PROMPT))
        (OUT / f"{sheet}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False))
        v = r["validation"]
        print(f"{sheet}: {len(r['plan']['operations'])} operations, {len(r['plan']['severity_rules'])} rules, "
              f"unsupported {v['unsupported']}, unmapped {v['unmapped_parts']}, missing {v['missing']}, "
              f"errors {v['errors']}, {r['seconds']}s")


if __name__ == "__main__":
    main()
