"""G8 clock-offset estimation + media retention classification."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.wonderecho_listener import estimate_clock_offset  # noqa: E402
from runtime.session_writer import classify_retention  # noqa: E402


def test_offset_uses_low_rtt_median():
    # (t0_edge, t1_server, t2_edge): true offset +100, one queue-delayed outlier.
    samples = [
        (1000, 1120, 1040),   # rtt 40, offset 100
        (2000, 2122, 2040),   # rtt 40, offset 102
        (3000, 3121, 3042),   # rtt 42, offset 100
        (4000, 4600, 4900),   # rtt 900 outlier (queued) — offset would be 150
    ]
    sync = estimate_clock_offset(samples)
    assert sync is not None
    assert 99 <= sync["offset_ms"] <= 102     # outlier excluded from the best half
    assert sync["rtt_ms"] == 40


def test_offset_empty_samples():
    assert estimate_clock_offset([]) is None


def test_retention_bargein_is_evidence():
    tier = classify_retention([{"barged_in": True, "status": "ok"}], "pi-123")
    assert tier["retention_tier"] == "T0_evidence"
    assert "barged_in" in tier["retention_reason"]


def test_retention_error_is_evidence():
    tier = classify_retention([{"status": "route_error"}], "pi-123")
    assert tier["retention_tier"] == "T0_evidence"


def test_retention_normal_session():
    utts = [{"status": "ok"}, {"status": "asr_only"}, {"status": "waiting_for_wake_word"}]
    assert classify_retention(utts, "pi-123") == {"retention_tier": "T1_normal"}


def test_retention_ci_session_is_evidence():
    assert classify_retention([{"status": "ok"}], "ci-replay-1")["retention_tier"] == "T0_evidence"
