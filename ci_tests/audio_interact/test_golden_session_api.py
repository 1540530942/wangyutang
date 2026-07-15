"""Golden session API compatibility checks for dashboard/vad_asr."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIO_INTERACT_DIR = REPO_ROOT / "audio_interact"
sys.path.insert(0, str(AUDIO_INTERACT_DIR))

# golden.py uses stdlib only — safe to import in CI without torch/fastapi.
from golden import list_golden_cases, resolve_golden_audio_path  # noqa: E402


def test_list_golden_cases_prefers_session_shape():
    cases = list_golden_cases()
    matching = [case for case in cases if case.get("case_id") == "vad_asr_simplex_001"]

    assert len(matching) == 1
    case = matching[0]
    assert case["session_id"] == "e34689be-8d7"
    assert case["audio_url"] == "/audio_interact/api/golden/audio/vad_asr_simplex_001"
    assert case["session_audio_url"] == "/audio_interact/api/golden/audio/e34689be-8d7"
    assert case["audio_file"] == "sessions/e34689be-8d7/audio/mic_proc_16k.wav"
    assert len(case["utterances"]) == 6
    assert case["utterances"][4]["expected_text"] == "往前走十五厘米。"
    assert case["utterances"][4]["expected_settings"]["unit_distance_cm"] == 15.0


def test_golden_audio_resolves_case_id_and_session_id_to_session_wav():
    by_case = resolve_golden_audio_path("vad_asr_simplex_001")
    by_session = resolve_golden_audio_path("e34689be-8d7")

    assert by_case is not None, "vad_asr_simplex_001 audio path not found"
    assert by_case.parts[-4:] == ("sessions", "e34689be-8d7", "audio", "mic_proc_16k.wav")
    assert by_session == by_case
