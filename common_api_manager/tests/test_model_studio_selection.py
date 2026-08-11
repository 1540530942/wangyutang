from __future__ import annotations

import tempfile
import unittest
import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

model_studio_router = import_module("modules.model_studio_catalog.router")


class ModelStudioSelectionTest(unittest.TestCase):
    def test_lv_selection_is_saved_with_robot_sandbox_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(model_studio_router, "SELECTION_FILE", Path(tmp) / "selection.json"):
            saved = model_studio_router.save_selection(
                model_studio_router.SelectionRequest(
                    llm=model_studio_router.ModelChoice(provider="lv"),
                    vision=model_studio_router.ModelChoice(provider="lv"),
                )
            )
            loaded = model_studio_router.load_selection()

        self.assertEqual(saved, loaded)
        self.assertEqual(loaded["llm"]["endpoint"], "/common/api/chat/qwen3/completions")
        self.assertEqual(loaded["llm"]["model"], "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
        self.assertEqual(loaded["vision"]["endpoint"], "/common/api/vision/lv/analyze-json")
        self.assertEqual(loaded["vision"]["model"], "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")

    def test_unknown_provider_falls_back_to_audio_defaults(self) -> None:
        selected = model_studio_router.default_selection()
        self.assertEqual(selected["llm"]["provider"], "dashscope")
        self.assertEqual(selected["vision"]["provider"], "spark")
        self.assertEqual(selected["vision"]["model"], model_studio_router.settings.spark_qwen_model)


if __name__ == "__main__":
    unittest.main()
