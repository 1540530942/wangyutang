"""Data-driven analyzer tests from JSON input/expected cases.

Run from pi5_monitor/:  python -m pytest tests/test_analyzer_cases.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyzer.analyzer import (  # noqa: E402
    _analyze_events,
    _event_summary,
    analyze_shutdown_snapshot,
)

DATA = os.path.join(os.path.dirname(__file__), "data", "analyzer_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


@pytest.mark.parametrize("c", DOC["snapshot_cases"], ids=[c["name"] for c in DOC["snapshot_cases"]])
def test_snapshot_case(c):
    out = analyze_shutdown_snapshot(c["input"])
    for token in c["expected_contains"]:
        assert token in out, f"{c['name']}: {token!r} not in {out!r}"


@pytest.mark.parametrize("c", DOC["events_cases"], ids=[c["name"] for c in DOC["events_cases"]])
def test_events_case(c):
    out = _analyze_events(c["input"])
    for token in c["expected_contains"]:
        assert token in out, f"{c['name']}: {token!r} not in {out!r}"


@pytest.mark.parametrize("c", DOC["summary_cases"], ids=[c["name"] for c in DOC["summary_cases"]])
def test_summary_case(c):
    out = _event_summary(c["input"])
    for token in c["expected_contains"]:
        assert token in out, f"{c['name']}: {token!r} not in {out!r}"
