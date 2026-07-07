"""Data-driven Caddyfile route-contract tests from JSON.

Run from robot_gateway/:  python -m pytest tests/test_route_cases.py -q
input=route path, expected=default upstream. Also asserts the retired
/audio and /interact routes are gone and static files exist.
"""
import json
import os

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADDYFILE = os.path.join(HERE, "Caddyfile")
DATA = os.path.join(os.path.dirname(__file__), "data", "route_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


def _text():
    with open(CADDYFILE, encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("c", DOC["route_cases"], ids=[c["path"] for c in DOC["route_cases"]])
def test_route_present_and_upstream(c):
    text = _text()
    assert f"handle_path {c['path']}" in text, f"missing route {c['path']}"
    assert c["upstream"] in text, f"route {c['path']} lost upstream {c['upstream']}"


def test_retired_routes_absent():
    text = _text()
    for path in DOC["retired_routes"]:
        assert f"handle_path {path}" not in text, f"retired route {path} still present"


def test_health_and_static():
    assert DOC["health_route"] in _text()
    for rel in DOC["static_files"]:
        assert os.path.isfile(os.path.join(HERE, rel)), f"missing {rel}"
