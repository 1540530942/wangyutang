"""Data-driven wake_state tests from JSON input/expected cases.

Run from audio_interact/:  python -m pytest tests/test_wake_cases.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wake_state import WakeStateStore, contains_wake_word  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "wake_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


@pytest.mark.parametrize("c", DOC["wake_word_cases"], ids=[c["text"] for c in DOC["wake_word_cases"]])
def test_wake_word(c):
    assert contains_wake_word(c["text"]) is c["expected"]


@pytest.mark.parametrize("c", DOC["sequence_cases"], ids=[c["name"] for c in DOC["sequence_cases"]])
def test_wake_sequence(c):
    store = WakeStateStore()
    dev = "pi-test"
    for op in c["ops"]:
        d = store.decide(dev, op["text"])
        exp = op["expected"]
        assert d.status == exp["status"], f"{c['name']}/{op['text']}: status={d.status} != {exp['status']}"
        if "should_route" in exp:
            assert d.should_route is exp["should_route"], f"{c['name']}/{op['text']}: should_route={d.should_route}"
        if "route_text" in exp:
            assert d.route_text == exp["route_text"], f"{c['name']}/{op['text']}: route_text={d.route_text!r}"
