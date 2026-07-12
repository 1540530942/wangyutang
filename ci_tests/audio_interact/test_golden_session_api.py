"""Golden session API compatibility checks for dashboard/vad_asr."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIO_INTERACT_DIR = REPO_ROOT / "audio_interact"
sys.path.insert(0, str(AUDIO_INTERACT_DIR))


def _load_audio_interact_server():
    module_name = "ci_audio_interact_server"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, AUDIO_INTERACT_DIR / "server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_list_golden_cases_prefers_session_shape():
    server = _load_audio_interact_server()

    cases = server.list_golden_cases()
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
    server = _load_audio_interact_server()

    by_case = server.get_golden_audio("vad_asr_simplex_001")
    by_session = server.get_golden_audio("e34689be-8d7")

    assert Path(by_case.path).parts[-4:] == ("sessions", "e34689be-8d7", "audio", "mic_proc_16k.wav")
    assert by_session.path == by_case.path
