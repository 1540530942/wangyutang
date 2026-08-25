from __future__ import annotations

import re
import threading
from dataclasses import dataclass


WAKE_WORD_VARIANTS = (
    "你好瓦力",
    "你好瓦利",
    "你好瓦里",
    "你好哇力",
    "你好哇利",
    "你好哇里",
    "你好挖力",
    "你好挖利",
    "你好挖里",
    "你好华力",
    "你好华利",
    "你好wall-e",
    "你好walle",
    "你好wallie",
    "你好阿里",  # ASR sometimes renders "瓦力" as "阿里" (e.g. "你好，阿里个。")
    "你好阿力",
    "你好瓦绿",  # ASR sometimes renders "瓦力" as "瓦绿"
    "你好娃力",
    "你好娃利",
)
DISMISS_PHRASES = ("退下吧", "退下", "退一下吧", "退下了")


@dataclass(frozen=True)
class WakeDecision:
    active: bool
    should_route: bool
    status: str
    route_text: str
    message: str = ""


class WakeStateStore:
    def __init__(self) -> None:
        self._active_devices: set[str] = set()
        self._lock = threading.Lock()

    def is_active(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self._active_devices

    def activate(self, device_id: str) -> None:
        """Force-activate a device — used for hardware wake signals (e.g., WonderEcho Pro UART)."""
        with self._lock:
            self._active_devices.add(device_id)

    def decide(self, device_id: str, text: str) -> WakeDecision:
        clean_text = normalize_text(text)
        if not clean_text:
            return WakeDecision(self.is_active(device_id), False, "empty", "")

        if contains_dismiss(clean_text):
            with self._lock:
                self._active_devices.discard(device_id)
            return WakeDecision(False, False, "sleeping", "", "dismissed")

        wake_span = find_wake_span(clean_text)
        if wake_span:
            route_text = clean_text[wake_span[1] :].strip()
            with self._lock:
                self._active_devices.add(device_id)
            return WakeDecision(True, bool(route_text), "awake", route_text, "wake_word")

        active = self.is_active(device_id)
        if active:
            return WakeDecision(True, True, "awake", text.strip())
        return WakeDecision(False, False, "sleeping", "", "waiting_for_wake_word")


def normalize_text(text: str) -> str:
    text = text.lower().replace("wall e", "walle")
    return re.sub(r"[\s,，.。!！?？:：;；、\"'“”‘’\-_\(\)（）\[\]【】]+", "", text)


def find_wake_span(clean_text: str) -> tuple[int, int] | None:
    candidates = [normalize_text(item) for item in WAKE_WORD_VARIANTS]
    for variant in candidates:
        index = clean_text.find(variant)
        if index >= 0:
            return index, index + len(variant)
    return None


def contains_wake_word(text: str) -> bool:
    return find_wake_span(normalize_text(text)) is not None


def contains_dismiss(clean_text: str) -> bool:
    return any(phrase in clean_text for phrase in (normalize_text(item) for item in DISMISS_PHRASES))
