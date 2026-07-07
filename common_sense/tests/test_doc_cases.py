"""Data-driven doc-integrity tests for common_sense from JSON contract.

Run from common_sense/:  python -m pytest tests/test_doc_cases.py -q
common_sense is reference documentation; the JSON records which referenced
files must exist (min size) and which service tokens the README must keep.
"""
import json
import os

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(HERE, "README.md")
DATA = os.path.join(os.path.dirname(__file__), "data", "doc_cases.json")
with open(DATA, encoding="utf-8") as fh:
    DOC = json.load(fh)


def _readme():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


@pytest.mark.parametrize("name", list(DOC["referenced_files_min_size"].keys()))
def test_referenced_file_exists_and_nonempty(name):
    path = os.path.join(HERE, name)
    assert os.path.isfile(path), f"missing {name}"
    assert os.path.getsize(path) >= DOC["referenced_files_min_size"][name], f"{name} too small"


@pytest.mark.parametrize("token", DOC["readme_must_mention"])
def test_readme_mentions_service(token):
    assert token in _readme(), f"README no longer mentions {token}"
