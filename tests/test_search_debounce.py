"""Tests for search debounce logic."""

import time


def test_debounce_cancels_previous():
    """Rapid calls should only execute the last one."""
    from ccsm.tui.screens.main import _DebounceTimer

    results = []
    timer = _DebounceTimer(delay=0.05, callback=lambda q: results.append(q))

    timer.schedule("a")
    timer.schedule("ab")
    timer.schedule("abc")

    time.sleep(0.15)  # Wait for debounce to fire

    # Only the last query should have fired
    assert results == ["abc"]


def test_debounce_fires_after_delay():
    """Single call should fire after the delay."""
    from ccsm.tui.screens.main import _DebounceTimer

    results = []
    timer = _DebounceTimer(delay=0.05, callback=lambda q: results.append(q))

    timer.schedule("hello")
    time.sleep(0.01)
    assert results == []  # Not yet

    time.sleep(0.1)
    assert results == ["hello"]  # Now fired
