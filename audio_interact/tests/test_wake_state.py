from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wake_state import WakeStateStore, contains_wake_word


def test_wake_word_accepts_homophones() -> None:
    assert contains_wake_word("你好，瓦力")
    assert contains_wake_word("你好 哇力")
    assert contains_wake_word("你好 walle")


def test_sleeping_device_ignores_commands_until_wake() -> None:
    store = WakeStateStore()
    decision = store.decide("pi-1", "前进")
    assert decision.status == "sleeping"
    assert not decision.should_route


def test_wake_word_activates_and_routes_following_text() -> None:
    store = WakeStateStore()

    wake = store.decide("pi-1", "你好瓦利")
    assert wake.status == "awake"
    assert not wake.should_route

    command = store.decide("pi-1", "前进")
    assert command.status == "awake"
    assert command.should_route
    assert command.route_text == "前进"


def test_wake_phrase_with_inline_command_routes_suffix() -> None:
    store = WakeStateStore()
    command = store.decide("pi-1", "你好瓦力向左转")
    assert command.status == "awake"
    assert command.should_route
    assert command.route_text == "向左转"


def test_dismiss_puts_device_back_to_sleep() -> None:
    store = WakeStateStore()
    store.decide("pi-1", "你好瓦力")
    dismiss = store.decide("pi-1", "退下吧")
    assert dismiss.status == "sleeping"
    assert not dismiss.should_route

    ignored = store.decide("pi-1", "前进")
    assert ignored.status == "sleeping"
    assert not ignored.should_route


if __name__ == "__main__":
    test_wake_word_accepts_homophones()
    test_sleeping_device_ignores_commands_until_wake()
    test_wake_word_activates_and_routes_following_text()
    test_wake_phrase_with_inline_command_routes_suffix()
    test_dismiss_puts_device_back_to_sleep()
    print("wake_state tests passed")
