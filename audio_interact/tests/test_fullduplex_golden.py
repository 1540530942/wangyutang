"""Golden validation of the full-duplex barge-in effect.

Proves the deployed behavior matches ground truth on the committed synthetic
golden session (tests/golden/sessions/fdx-bargein-synthetic-001):

1. the offline eval (evaluate_bargein) detects the real barge-in inside the
   expected window and raises NO false barge-in on the echo-only negative case;
2. the dashboard extractor (_extract_bargein_cases) reports the SAME commit plus
   cancel-path metrics (commit_delay, post_cancel_tail) within spec thresholds;
3. the golden package validates and can be regenerated deterministically.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="ai_fdx_golden_")
os.environ.setdefault("AUDIO_INTERACT_DATA_DIR", _TMP)
os.environ.setdefault("DATA_DIR", _TMP)

from eval.report_generator import generate_report  # noqa: E402
from tools.validate_session import validate_session  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden" / "sessions" / "fdx-bargein-synthetic-001"


def _case(report_cases, case_id):
    return next(c for c in report_cases if c["case_id"] == case_id)


def test_golden_package_is_valid():
    assert GOLDEN.is_dir(), "run tests/golden/make_fullduplex_golden.py to generate the pack"
    assert validate_session(GOLDEN) == []


def test_offline_eval_detects_bargein_and_no_false_positive(tmp_path):
    # Write the report to tmp so the committed golden package stays pristine.
    rep = generate_report(GOLDEN, output_path=tmp_path / "eval_result.json")
    be = rep["bargein_eval"]
    assert be["case_count"] == 2 and be["commit_count"] == 1

    pos = _case(be["cases"], "bargein_middle_001")
    assert pos["detected"] and not pos["false_bargein"] and not pos["missed_bargein"]
    assert not pos["early_interrupt"] and not pos["late_interrupt"]   # commit inside window
    assert pos["commit_ms"] == 5287

    neg = _case(be["cases"], "echo_only_001")
    assert not neg["detected"] and not neg["false_bargein"]           # echo alone never interrupts

    # Pipeline sanity: the synthetic transcripts/segments line up with labels.
    assert rep["vad_eval"]["speech_recall"] == 1.0
    assert rep["asr_eval"]["cer"] == 0.0


def test_dashboard_extractor_matches_eval_and_thresholds():
    import server

    events = server._load_events_for_session(GOLDEN)
    cases, cancelled = server._extract_bargein_cases(events)

    # Exactly one committed barge-in; the interrupted turn is flagged.
    assert len(cases) == 1
    assert cancelled == {"turn_001"}

    c = cases[0]
    assert c["tts_id"] == "tts_001"
    assert c["commit_ms"] == 5287                    # same fact the offline eval validated
    assert c["commit_delay_ms"] == 187               # speech_start(5100) → commit(5287)
    assert c["has_edge_telemetry"] is True
    assert c["post_cancel_tail_ms"] == 88            # cancel_recv(5300) → play_stop(5388)

    # Spec acceptance metrics (docs/audio-interact-fullduplex-golden-spec.md).
    assert c["post_cancel_tail_ms"] <= 150           # target ≤150, max ≤250

    # G8 cross-clock: with clock.sync (offset 0 in this pack) the end-to-end
    # cancel_delay = play_stop(5388) − speech_start(5100) = 288ms ≤ 300 target.
    assert c["clock_offset_ms"] == 0
    assert c["cancel_delay_ms"] == 288
    assert c["cancel_delay_ms"] <= 300
