"""Real example responses for every API endpoint, for building the frontend against exact shapes.

python scripts/dev/api_fixtures.py [--out docs/api_fixtures.json]

Builds the app with fake models (the same fakes the tests use) on a temporary database, walks a full day (session,
garment, photo, review, decision, voice, completion, trend alert, audit, report, agent trace, demo endpoints when
present) and writes each request and response. Ids and timestamps are whatever the fake run produced.
"""
import argparse
import io
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline import FakeEnsemble, fake_sam  # noqa: E402

from stitchcheck.backend.app import Deps, create_app  # noqa: E402

VERDICT = {"verdict": "confirm", "label": "skipped_stitch", "severity_judgement": None,
           "reason": "One stitch is longer than its neighbours where the thread floats.", "confidence": 0.8}


class Spark:
    """Inspect calls get the tool flow; voice prompts get a parsed correction; report prompts get a short report."""

    def __init__(self):
        self.chat = self.completions = self

    def create(self, messages, **kw):
        first = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        if "spoke a correction" in first:
            content, calls = json.dumps({"label": None, "part": "collar", "severity": "major", "machine_id": "7",
                                         "ambiguous": ["label"], "label_candidates": ["skipped_stitch", "broken_stitch"]}), None
        elif "work order" in first:
            content, calls = "Check machine {{alert.machine}} for {{alert.defect}}: needle, timing, thread path.", None
        elif "quality report" in first:
            done = sum(1 for m in messages if m["role"] == "tool")
            if not done:
                return _reply(None, [_call("c1", "get_dhu", {"scope": "session"})])
            content, calls = "## Buyer summary\n- Garments inspected: {{dhu.session.garments}}\n- DHU: {{dhu.session.dhu}}", None
        elif messages[-1]["role"] != "tool":
            return _reply(None, [_call("c1", "classify_stitch", {})])
        else:
            content, calls = json.dumps(VERDICT), None
        return _reply(content, calls)


def _call(cid, name, args):
    return type("Call", (), dict(id=cid, function=type("F", (), dict(name=name, arguments=json.dumps(args)))()))()


def _reply(content, calls):
    msg = type("Msg", (), dict(content=content, tool_calls=calls,
                               model_dump=lambda self, **k: {"role": "assistant", "content": content}))()
    return type("R", (), dict(choices=[type("C", (), dict(message=msg))()],
                              usage=type("U", (), dict(prompt_tokens=120, completion_tokens=40))()))()


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 200, 210)).save(buf, format="JPEG")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/api_fixtures.json")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    ref = tmp / "ref.jpg"
    Image.new("RGB", (224, 224)).save(ref)
    import stitchcheck.spark.inspect as hybrid
    hybrid.CACHE_DIR = tmp / "cache"
    skipped = [0.05, 0.85, 0.05, 0.03, 0.02]
    deps = Deps(db_path=str(tmp / "f.db"), storage=str(tmp / "photos"), sam=fake_sam(parts={"pocket": 0.9}),
                ensemble=FakeEnsemble(skipped), spark=Spark(), spark_open=None,
                transcribe=lambda p: "machine ఏడు collar దగ్గర కుట్లు చేరాయి ఇది major", reference=lambda f: ref)
    exts = []
    for mod, fn in (("stitchcheck.backend.access", "install"), ("stitchcheck.backend.demo_api", "register")):
        try:
            exts.append(getattr(__import__(mod, fromlist=[fn]), fn))
        except (ImportError, AttributeError):
            pass
    c = TestClient(create_app(deps, extensions=exts))
    out = []

    def call(method, path, **kw):
        r = c.request(method, path, **kw)
        body = kw.get("json")
        try:
            resp = r.json()
        except ValueError:
            resp = r.text[:200]
        out.append({"request": f"{method} {path}", "body": body, "status": r.status_code, "response": resp})
        return resp

    call("GET", "/health")
    call("GET", "/version")
    call("GET", "/brains")
    plan = [dict(part=p, operator_id=f"OP{i:02d}", machine_id=str(m)) for i, (p, m) in
            enumerate([("collar", 7), ("placket", 12), ("cuff", 3)], 1)]
    s = call("POST", "/sessions", json=dict(date="2026-11-01", style="Men's shirt", fabric="A", lot_size=500,
                                             line_plan=plan, critical_parts=["collar", "placket"], is_demo=False,
                                             brain="spark"))
    call("GET", f"/sessions/{s['id']}")
    call("POST", f"/sessions/{s['id']}/brain", json={"brain": "spark"})
    b = call("POST", f"/sessions/{s['id']}/bundles", json={"bundle_no": 1})
    garments = []
    for n in (1, 2, 3):
        g = call("POST", f"/bundles/{b['id']}/garments", json={"garment_no": n})
        garments.append(g)
        r = call("POST", f"/garments/{g['id']}/photos", files={"image": ("p.jpg", jpeg(), "image/jpeg")},
                 data={"part": "collar", "reason": "critical"})
        insp = call("GET", f"/inspections/{r['inspection_id']}")
        if n == 1:
            call("GET", f"/agent-runs/{insp['agent_run_id']}")
            call("POST", f"/inspections/{insp['id']}/voice", files={"audio": ("v.webm", b"fake", "audio/webm")})
            call("POST", f"/inspections/{insp['id']}/decision", json={"action": "change", "label": "skipped_stitch",
                                                                      "part": "collar", "via": "voice"})
        else:
            call("POST", f"/inspections/{insp['id']}/decision", json={"action": "agree", "via": "tap"})
        call("POST", f"/garments/{g['id']}/complete")
    call("GET", f"/sessions/{s['id']}/dashboard")
    a = call("POST", f"/sessions/{s['id']}/audit")
    call("GET", f"/sessions/{s['id']}/audit")
    rep = call("POST", f"/sessions/{s['id']}/report")
    call("GET", f"/reports/{rep['id']}")
    call("POST", "/demo/session")
    call("GET", "/demo/gallery")
    out.append({"note": "Errors: 404 not found, 409 still reviewing or conflict, 413 too large, 422 invalid input "
                        "(message in detail), 429 too many requests, 503 AI or seam finder unavailable."})
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(f"wrote {len(out)} examples to {args.out}; statuses:",
          sorted({f"{e['request'].split(' ')[0]} {e['status']}" for e in out if "status" in e}))


if __name__ == "__main__":
    main()
