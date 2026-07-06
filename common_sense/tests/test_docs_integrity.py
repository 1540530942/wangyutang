"""Doc-integrity tests for the common_sense reference module.

Run from the common_sense/ directory:  python -m pytest tests/ -q
common_sense holds no code — it is cross-cutting reference documentation. There
is nothing to unit-test in the behavioural sense, so instead these are honest
regression guards that fail if the README points at a file that no longer
exists or the topology diagram is emptied out.
"""
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(HERE, "README.md")


def _readme_text():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def test_readme_exists():
    assert os.path.isfile(README)


def test_referenced_files_exist():
    """Every `filename.html` mentioned in the README must exist on disk."""
    referenced = set(re.findall(r"`([\w./-]+\.html)`", _readme_text()))
    assert referenced, "README references no doc files — layout changed?"
    for name in referenced:
        assert os.path.isfile(os.path.join(HERE, name)), f"missing {name}"


def test_topology_html_nonempty():
    html = os.path.join(HERE, "network_topology_remote_access.html")
    assert os.path.getsize(html) > 100  # not truncated to a stub


def test_topology_diagram_lists_core_services():
    """The ASCII overview in the README must still mention the gateway routes,
    so the doc stays in sync with the deployed service set."""
    text = _readme_text()
    for token in ("camera-snapshot", "action-move", "audio-recognition",
                  "smile-face", "pi5-robot"):
        assert token in text, f"topology overview dropped {token}"
