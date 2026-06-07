"""Offline test for the press-pattern state machine (no GPIO needed).

Run: python3 tests/test_press_pattern.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from walkman.button import PressPattern  # noqa: E402

WINDOW = 0.2


def collect(actions):
    events = []
    p = PressPattern(on_gesture=events.append, double_window=WINDOW)
    actions(p)
    time.sleep(WINDOW + 0.15)  # let any pending double-click timer resolve
    return events


def main():
    # single: one short press, nothing within the window
    assert collect(lambda p: p.short_press()) == ["single"]

    # double: two short presses within the window
    def two(p):
        p.short_press(); time.sleep(WINDOW / 3); p.short_press()
    assert collect(two) == ["double"]

    # long: a held press
    assert collect(lambda p: p.long_press()) == ["long"]

    # triple (3+) collapses to a single "double" event
    def three(p):
        p.short_press(); p.short_press(); p.short_press()
    assert collect(three) == ["double"]

    # two singles separated by more than the window -> two "single" events
    def spaced(p):
        p.short_press(); time.sleep(WINDOW + 0.15); p.short_press()
    assert collect(spaced) == ["single", "single"]

    print("PressPattern: all cases OK (single / double / long / triple / spaced)")


if __name__ == "__main__":
    main()
