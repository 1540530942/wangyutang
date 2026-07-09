from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TTSState:
    playing: bool = False
    tts_id: str = ""
    text: str = ""

    def update(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in {"tts.play_start", "tts.request", "tts.audio_ready"}:
            self.tts_id = str(event.get("tts_id") or self.tts_id)
            self.text = str(event.get("text") or self.text)
            if event_type == "tts.play_start":
                self.playing = True
        elif event_type in {"tts.stop", "tts.play_end"}:
            self.playing = False

    def snapshot(self) -> dict[str, Any]:
        return {"playing": self.playing, "tts_id": self.tts_id, "text": self.text}

