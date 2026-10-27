"""Mechanic work order from a trend alert: Muse Spark drafts it in Telugu and English from the checklist.

Same rules as the report agent: numbers and IDs only through placeholders the server fills in, checked by the
validator (one retry), and process first (machine and method, never a person).
"""
import time

from stitchcheck.backend.report import CHECKLIST, render, validate

PROMPT = """Write a short work order for the unit's mechanic, in Telugu first and then English.
A trend alert fired: the same defect was confirmed several times on one machine within its recent garments.
Facts (use only placeholders for numbers and IDs; never type a digit or an ID):
- {{{{alert.machine}}}}: the machine number; always write it as "machine {{{{alert.machine}}}}" in English and "మెషిన్ {{{{alert.machine}}}}" in Telugu
- {{{{alert.defect}}}}: the defect
- {{{{alert.count}}}}: how many garments showed it
- {{{{alert.window}}}}: within how many recent garments
Checks to make, in order (translate them for the Telugu part):
{checks}
Rules: talk about the machine and the method, never about a person; phrase every line as a check to make; at most
about 80 words per language; no dashes as punctuation. Reply with only the work order text."""


def draft_work_order(client, machine, defect, garment_count, window, model, effort):
    """Returns dict(template, body_md, validator_ok, errors, facts, tokens, seconds)."""
    t0 = time.time()
    facts = {"alert.machine": str(machine), "alert.defect": defect.replace("_", " "), "alert.count": str(garment_count),
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
        if not errors:
            break
        msgs += [{"role": "assistant", "content": template},
                 {"role": "user", "content": "Fix these and reply with the whole work order again:\n- " + "\n- ".join(errors)}]
    return dict(template=template, body_md=render(template, facts), validator_ok=not errors, errors=errors, facts=facts,
                tokens=(tin, tout), seconds=round(time.time() - t0, 2))
