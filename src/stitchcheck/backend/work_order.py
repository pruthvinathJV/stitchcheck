"""Mechanic work order from a trend alert: Muse Spark drafts it in Telugu and English from the checklist.

Same rules as the report agent: numbers and IDs only through placeholders the server fills in, checked by the
validator (one retry), and process first (machine and method, never a person).
"""
import time

from stitchcheck.backend.report import CHECKLIST, render, validate

PROMPT = """Write a short work order for the unit's mechanic: the Telugu version, then a line with only ===, then the
English version. The app already shows the machine and the defect as a title, so write no title line and never
mention the machine again in the lines.
A trend alert fired: the same defect was confirmed several times on one machine within its recent garments.
Facts (use only placeholders for numbers and IDs; never type a digit or an ID):
- {{{{alert.defect}}}}: the defect
- {{{{alert.count}}}}: how many garments showed it
- {{{{alert.window}}}}: within how many recent garments
Each version: first the fact line, then one check per line, in this order:
{checks}
The English fact line is exactly this, without quotes: {{{{alert.defect}}}} on {{{{alert.count}}}} of the last {{{{alert.window}}}} garments.
The Telugu fact line says the same in plain Telugu. Translate the checks for the Telugu version.
Rules: talk about the machine and the method, never about a person; each English check starts with a verb ("Check",
"Replace"), and each Telugu check reads naturally with the verb at the end ("... సరిచూడండి");
at most about 60 words per language; no dashes as punctuation; no bullets or headings. Reply with only the work order."""

SEPARATOR = "\n===\n"


def draft_work_order(client, machine, defect, garment_count, window, model, effort):
    """Returns dict(template, body_md, body_te, body_en, validator_ok, errors, facts, tokens, seconds). body_te and
    body_en are None when the model did not separate the two languages; body_md always holds the whole text."""
    t0 = time.time()
    facts = {"alert.machine": str(machine), "alert.defect": defect.replace("_", " ").capitalize(), "alert.count": str(garment_count),
             "alert.window": str(window)}
    checks = "\n".join(f"- {c}" for c in CHECKLIST.get(defect, ["Inspect the machine set-up for this defect"]))
    msgs = [{"role": "user", "content": PROMPT.format(checks=checks)}]
    tin = tout = 0
    template, errors = "", ["no work order written"]
    for attempt in (1, 2):
        r = client.chat.completions.create(model=model, messages=msgs, reasoning_effort=effort)
        tin, tout = tin + r.usage.prompt_tokens, tout + r.usage.completion_tokens
        template = r.choices[0].message.content or ""
        errors = validate(template, facts)
        layout = [] if SEPARATOR in f"\n{template.strip()}\n" else ["put a line with only === between the two versions"]
        if not errors and not layout:
            break
        msgs += [{"role": "assistant", "content": template},
                 {"role": "user", "content": "Fix these and reply with the whole work order again:\n- " + "\n- ".join(errors + layout)}]
    body = "\n".join(ln.strip().strip('"') for ln in render(template, facts).strip().splitlines())
    te, sep, en = ("\n" + body + "\n").partition(SEPARATOR)
    te, en = (te.strip(), en.strip()) if sep else (None, None)
    return dict(template=template, body_md=f"{te}\n\n{en}" if sep else body, body_te=te, body_en=en,
                validator_ok=not errors, errors=errors, facts=facts, tokens=(tin, tout), seconds=round(time.time() - t0, 2))
