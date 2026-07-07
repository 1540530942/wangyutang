"""Data-driven camera_snapshot pure-function tests from JSON input/expected.

Run from camera_snapshot/:  python -m pytest tests/test_server_pure_cases.py -q
raises=true expects an HTTPException (400) from the function.
"""
import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data", "server_pure_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


@pytest.mark.parametrize("c", DOC["normalize_kind_cases"], ids=[str(c["value"]) for c in DOC["normalize_kind_cases"]])
def test_normalize_kind(c):
    if c.get("raises"):
        with pytest.raises(HTTPException):
            server.normalize_kind(c["value"])
    else:
        assert server.normalize_kind(c["value"]) == c["expected"]


@pytest.mark.parametrize("c", DOC["infer_kind_cases"], ids=[c["source"] for c in DOC["infer_kind_cases"]])
def test_infer_kind(c):
    assert server.infer_kind_from_source(c["source"]) == c["expected"]


@pytest.mark.parametrize("c", DOC["mode_cases"], ids=[f'{c["mode"]}/{c["kind"]}' for c in DOC["mode_cases"]])
def test_mode_to_kind_and_mode(c):
    if c.get("raises"):
        with pytest.raises(HTTPException):
            server.mode_to_kind_and_mode(c["mode"], c["kind"])
    else:
        assert list(server.mode_to_kind_and_mode(c["mode"], c["kind"])) == c["expected"]


@pytest.mark.parametrize("c", DOC["gpio_cases"], ids=[str(c["value"]) for c in DOC["gpio_cases"]])
def test_normalize_gpio(c):
    if c.get("raises"):
        with pytest.raises(HTTPException):
            server.normalize_gpio(c["value"])
    else:
        assert server.normalize_gpio(c["value"]) == c["expected"]


@pytest.mark.parametrize("c", DOC["throttle_cases"], ids=[c["value"] for c in DOC["throttle_cases"]])
def test_throttled_flags(c):
    assert server.throttled_flags(c["value"]) == set(c["expected"])
