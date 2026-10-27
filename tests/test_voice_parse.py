import json

import pytest

from stitchcheck.backend.voice_parse import normalise_numbers, parse_correction

PLAN = [dict(part="collar", operator_id="OP01", machine_id="7"), dict(part="placket", operator_id="OP02", machine_id="12")]


class Reply:
    def __init__(self, out):
        self.out, self.prompts = out, []
        self.chat = self.completions = self

    def create(self, model, messages, **kw):
        self.prompts.append(messages[0]["content"])
        msg = type("M", (), dict(content=json.dumps(self.out)))()
        return type("R", (), dict(choices=[type("C", (), dict(message=msg))()],
                                  usage=type("U", (), dict(prompt_tokens=10, completion_tokens=5))()))()


@pytest.mark.parametrize("text, out", [
    ("machine ఏడు", "machine 7"), ("machine పన్నెండు bundle ముప్పై నాలుగు", "machine 12 bundle 34"),
    ("machine ఇరవై ఐదు", "machine 25"), ("Machine Seven", "machine 7".replace("m", "M", 1)),
    ("machine twenty-one", "machine 21"), ("bundle ninety nine", "bundle 99"),
    ("ఏడుస్తున్నారు", "ఏడుస్తున్నారు"),  # a longer word that starts with ఏడు is left alone
])
def test_number_words_to_digits(text, out):
    assert normalise_numbers(text) == out


def test_spark_sees_digits_and_machine_numbers_match_as_strings():
    client = Reply({"label": "broken_stitch", "machine_id": 7, "part": "collar", "ambiguous": []})
    r = parse_correction(client, "machine ఏడు collar దగ్గర తెగింది", PLAN)
    assert "machine 7 collar" in client.prompts[0]
    assert r["proposed"] == {"label": "broken_stitch", "part": "collar", "machine_id": "7"} and r["ambiguous"] == []


def test_unclear_defect_returns_candidates_for_one_tap_buttons():
    client = Reply({"label": None, "machine_id": "9", "ambiguous": ["label"],
                    "label_candidates": ["skipped_stitch", "broken_stitch", "skipped_stitch", "hole", "crooked_seam"]})
    r = parse_correction(client, "machine తొమ్మిది కుట్లు చేరాయి", PLAN)
    assert r["label_candidates"] == ["skipped_stitch", "broken_stitch", "crooked_seam"]
    assert set(r["ambiguous"]) == {"label", "machine_id"} and "machine_id" not in r["proposed"]  # machine 9 not in plan
