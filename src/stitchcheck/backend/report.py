"""End-of-day report agent: Muse Spark calls read-only tools, writes the report with placeholders, the server fills
them in (docs/backend_spec.md, section 7).

Every tool output is flattened into facts keyed by a path (e.g. dhu.operator.OP07.dhu) and each output lists the
paths it provides. Spark writes {{path}} wherever a number or ID goes. The validator rejects a template that uses
an unknown path or contains any digit or ID outside a placeholder (apart from a few constants such as AQL 2.5);
on failure Spark gets the error list and one retry. Numbers are therefore never typed by the model.
"""
import json
import re
import time

from stitchcheck.backend import db
from stitchcheck.meta_api import SPARK_MODEL
from stitchcheck.qc import counts

# Draft checks per defect, phrased as checks, never blame. To be replaced by the unit's own table.
CHECKLIST = {
    "skipped_stitch": ["Needle size and type suit the thread and fabric", "Needle is straight, sharp and fully inserted",
                       "Hook timing and needle-to-hook gap", "Thread path and take-up spring"],
    "broken_stitch": ["Upper and bobbin thread tension", "Needle eye and throat plate for burrs", "Thread quality and lubrication",
                      "Needle size for the thread"],
    "crooked_seam": ["Edge guide or folder setting", "Presser foot pressure and feed dog height", "Handling: let the feed move the fabric"],
    "puckering": ["Thread tension (often too tight)", "Stitch length", "Presser foot pressure and differential feed",
                  "Thread and fabric shrinkage match"],
    "stain_damage": ["Machine oil leaks around needle bar and hook", "Clean hands, table and bins", "Cover finished pieces"],
    "open_seam": ["Seam allowance and edge guide", "Stitch density", "Thread tension and needle condition"],
}
CHECKLIST_STATUS = "draft"
CONSTANTS = ["ISO 2859-1", "AQL 2.5", "AQL 4.0", "2.5", "4.0"]  # the only digits allowed outside placeholders
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")
DIGIT = re.compile(r"[0-9౦-౯]")  # Western and Telugu digits
ID_LIKE = re.compile(r"\b(?:OP|M)-?\d+\b", re.IGNORECASE)
MAX_ROUNDS = 8

SYSTEM = (
    "You write the end-of-day quality report for a garment stitching unit, from tool results only.\n"
    "Call the tools you need (get_dhu, get_top_issues, get_audit_status, get_examples, get_ai_vs_checker, get_checklist). Each result "
    "lists 'facts': paths and their values. Wherever the report needs a number, ID or count, write the placeholder "
    "{{path}} with a path from those facts. Never type a digit or an ID yourself; the server fills placeholders in. "
    "Allowed without placeholders: ISO 2859-1, AQL 2.5, AQL 4.0.\n"
    "Defects are about the process, not the person: never name, rank or address any worker, and never write 'worst'. "
    "Talk about machines and operations.\n"
    "Write markdown with three sections:\n"
    "## Buyer summary (English): garments inspected, DHU, defective %, audit result if any, top defects with counts, "
    "photo evidence references.\n"
    "## Supervisor actions (Telugu, then English): for the machines and operations with the most defects, the defect "
    "pattern and the checks from get_checklist (needle, tension, feed, handling), as checks for the mechanic or the "
    "line, never as blame.\n"
    "## Data notes (English): AI-proposed vs checker-confirmed counts, from get_ai_vs_checker.\n"
    "Reply with only the markdown when done."
)

TOOLS = [
    {"type": "function", "function": {"name": "get_dhu", "description": "DHU and defect counts for the session, or split by machine, part or defect.",
     "parameters": {"type": "object", "properties": {"scope": {"type": "string", "enum": ["session", "machine", "part", "defect"]}},
                    "required": ["scope"]}}},
    {"type": "function", "function": {"name": "get_top_issues", "description": "Machine, operation and defect combinations with the most defects, with counts.",
     "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}}},
    {"type": "function", "function": {"name": "get_audit_status", "description": "AQL audit plan and status, if an audit was run.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_examples", "description": "Checker-confirmed photo IDs for a defect, for evidence links.",
     "parameters": {"type": "object", "properties": {"defect": {"type": "string"}, "n": {"type": "integer"}}, "required": ["defect", "n"]}}},
    {"type": "function", "function": {"name": "get_ai_vs_checker", "description": "How the AI proposals fared: photos, agreed, changed, not a defect, sent to the checker, pending.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_checklist", "description": "Practical machine and handling checks for a defect.",
     "parameters": {"type": "object", "properties": {"defect": {"type": "string"}}, "required": ["defect"]}}},
]


def _fmt(v):
    """Numbers to one decimal; labels and parts readable ("skipped_stitch" -> "skipped stitch")."""
    return f"{v:.1f}" if isinstance(v, float) else str(v).replace("_", " ")


def _label(defect):
    """Tool arguments may use the readable name the facts show ("skipped stitch"); tables use the label."""
    return str(defect).strip().lower().replace(" ", "_")


class Tools:
    """Read-only tools over one session. Every call adds its values to self.facts."""

    def __init__(self, con, session_id):
        self.con, self.sid, self.facts = con, session_id, {}
        self.insp = db.session_inspections(con, session_id)
        self.done = db.completed_garments(con, session_id)

    def _add(self, values):
        self.facts.update({k: _fmt(v) for k, v in values.items()})
        return {"facts": {k: _fmt(v) for k, v in values.items()}}

    def get_dhu(self, scope="session"):
        if scope not in ("session", "machine", "part", "defect"):
            return {"error": "scopes: session, machine, part, defect"}  # no per-person numbers in reports
        if scope == "session":
            confirmed = counts.confirmed_defects(self.insp, self.done)
            return self._add({"dhu.session.dhu": counts.dhu(self.insp, self.done),
                              "dhu.session.defective_pct": counts.defective_pct(self.insp, self.done),
                              "dhu.session.garments": len(self.done), "dhu.session.defects": len(confirmed)})
        values = {}
        for key, v in counts.dhu_by(self.insp, self.done, scope).items():
            values[f"dhu.{scope}.{key}.dhu"] = v["dhu"]
            values[f"dhu.{scope}.{key}.defects"] = v["defects"]
            values[f"dhu.{scope}.{key}.name"] = key
        return self._add(values)

    def get_top_issues(self, n=3):
        values = {}
        for i, (mc, part, defect, c) in enumerate(counts.process_issues(self.insp, self.done, n), 1):
            values |= {f"top.{i}.machine": mc, f"top.{i}.part": part, f"top.{i}.defect": defect, f"top.{i}.count": c}
        return self._add(values)

    def get_audit_status(self):
        row = self.con.execute("SELECT * FROM audit WHERE session_id = ? ORDER BY id DESC", (self.sid,)).fetchone()
        if row is None:
            return self._add({"audit.status": "no audit run"})
        return self._add({"audit.status": row["status"], "audit.lot_size": row["lot_size"],
                          "audit.major.sample_size": row["plan"]["major"]["sample_size"],
                          "audit.major.ac": row["plan"]["major"]["ac"], "audit.major.re": row["plan"]["major"]["re"]})

    def get_examples(self, defect, n=3):
        defect = _label(defect)
        ids = [i["id"] for i in counts.confirmed_defects(self.insp, self.done) if i["final_label"] == defect][:n]
        return self._add({f"examples.{defect}.{k}": f"photo {i}" for k, i in enumerate(ids, 1)})

    def get_ai_vs_checker(self):
        n = lambda pred: sum(1 for i in self.insp if pred(i))
        return self._add({"ai.photos": len(self.insp), "ai.agreed": n(lambda i: i["decision"] == "agree"),
                          "ai.changed": n(lambda i: i["decision"] == "changed"),
                          "ai.not_a_defect": n(lambda i: i["decision"] == "not_a_defect"),
                          "ai.sent_to_checker": n(lambda i: i["needs_checker"]),
                          "ai.pending": n(lambda i: i["decision"] == "pending")})

    def get_checklist(self, defect):
        defect = _label(defect)
        return {"defect": defect, "checks": CHECKLIST.get(defect, []), "status": CHECKLIST_STATUS}

    def call(self, name, args):
        if name not in {t["function"]["name"] for t in TOOLS}:
            return {"error": f"unknown tool {name}"}
        return getattr(self, name)(**args)


def validate(template, facts):
    """Errors in a report template: unknown placeholders, and digits or IDs outside placeholders."""
    errors = [f"unknown placeholder {{{{{p}}}}}" for p in PLACEHOLDER.findall(template) if p not in facts]
    bare = PLACEHOLDER.sub(" ", template)
    for c in CONSTANTS:
        bare = bare.replace(c, " ")
    bare = re.sub(r"^#+ ", "", bare, flags=re.MULTILINE)
    for m in ID_LIKE.finditer(bare):
        errors.append(f"ID typed outside a placeholder: {m.group(0)!r}")
    bare = ID_LIKE.sub(" ", bare)
    lines = [ln.strip() for ln in bare.splitlines() if DIGIT.search(ln)]
    errors += [f"number typed outside a placeholder: {ln[:80]!r}" for ln in lines]
    return errors


def render(template, facts):
    return PLACEHOLDER.sub(lambda m: facts.get(m.group(1), m.group(0)), template)


def run_report(client, con, session_id, model=SPARK_MODEL, effort="minimal"):
    """The multi-step agent. Returns dict(template, body_md, validator_ok, errors, trace, tokens, seconds)."""
    tools, t0 = Tools(con, session_id), time.time()
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Write today's report."}]
    calls, outputs, tin, tout = [], [], 0, 0
    template, errors = "", ["no report written"]
    for attempt in (1, 2):
        for _ in range(MAX_ROUNDS):
            r = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS, reasoning_effort=effort)
            tin, tout = tin + r.usage.prompt_tokens, tout + r.usage.completion_tokens
            m = r.choices[0].message
            if not m.tool_calls:
                template = m.content or ""
                break
            msgs.append(m.model_dump(exclude_none=True))
            for tc in m.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                out = tools.call(tc.function.name, args)
                calls.append({"name": tc.function.name, "arguments": args})
                outputs.append(out)
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out, ensure_ascii=False)})
        errors = validate(template, tools.facts)
        if not errors:
            break
        msgs += [{"role": "assistant", "content": template},
                 {"role": "user", "content": "The report failed validation. Fix every item and reply with the whole "
                                             "report again:\n- " + "\n- ".join(errors)}]
    return dict(template=template, body_md=render(template, tools.facts), validator_ok=not errors, errors=errors,
                trace=dict(tool_calls=calls, tool_outputs=outputs), facts=tools.facts, tokens=(tin, tout),
                seconds=round(time.time() - t0, 2))
