"""Press-pattern state machine: single / double / long / triple / spaced."""
import time
import unittest

import _path  # noqa: F401
from walkman.button import PressPattern

WINDOW = 0.15


def collect(actions):
    events = []
    p = PressPattern(on_gesture=events.append, double_window=WINDOW)
    actions(p)
    time.sleep(WINDOW + 0.12)  # let the double-click timer resolve
    return events


class PressPatternTest(unittest.TestCase):
    def test_single(self):
        self.assertEqual(collect(lambda p: p.short_press()), ["single"])

    def test_double(self):
        def two(p):
            p.short_press(); time.sleep(WINDOW / 3); p.short_press()
        self.assertEqual(collect(two), ["double"])

    def test_long(self):
        self.assertEqual(collect(lambda p: p.long_press()), ["long"])

    def test_triple_collapses_to_double(self):
        def three(p):
            p.short_press(); p.short_press(); p.short_press()
        self.assertEqual(collect(three), ["double"])

    def test_spaced_singles(self):
        def spaced(p):
            p.short_press(); time.sleep(WINDOW + 0.12); p.short_press()
        self.assertEqual(collect(spaced), ["single", "single"])

    def test_long_suppresses_pending_clicks(self):
        # a long press cancels any queued click so it never also fires single/double
        events = []
        p = PressPattern(on_gesture=events.append, double_window=WINDOW)
        p.short_press()      # would become a single...
        p.long_press()       # ...but a long press cancels it
        time.sleep(WINDOW + 0.12)
        self.assertEqual(events, ["long"])


if __name__ == "__main__":
    unittest.main()
