"""LED status engine: mode -> RGB rendering, blink phases, and the shutdown latch."""
import unittest

import _path  # noqa: F401
from walkman import led


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.s = led.LedStatus(max_brightness=1.0, breathe_period_s=4.0)

    def test_playing_is_green_and_breathes(self):
        a = self.s._render(led.PLAYING, 0.0)
        b = self.s._render(led.PLAYING, 1.0)   # quarter cycle later
        self.assertEqual((a[0], a[2]), (0, 0))  # no red/blue
        self.assertEqual((b[0], b[2]), (0, 0))
        self.assertNotEqual(a[1], b[1])         # green level changes over time

    def test_paused_is_steady_amber(self):
        r, g, b = self.s._render(led.PAUSED, 0.0)
        self.assertGreater(r, 0)
        self.assertGreater(g, 0)
        self.assertEqual(b, 0)
        self.assertEqual(self.s._render(led.PAUSED, 5.0), (r, g, b))  # steady

    def test_shutdown_is_full_white_regardless_of_brightness(self):
        dim = led.LedStatus(max_brightness=0.2)
        self.assertEqual(dim._render(led.SHUTDOWN, 0.0), led.WHITE)

    def test_blink_modes_have_on_and_off_phases(self):
        for mode in (led.STARTUP, led.ERROR, led.REAUTH):
            vals = {self.s._render(mode, t) for t in (0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.3)}
            self.assertIn(led.OFF, vals, f"{mode} should have an off phase")
            self.assertTrue(any(v != led.OFF for v in vals), f"{mode} should have an on phase")

    def test_blink_helper(self):
        self.assertTrue(led.LedStatus._blink(0.0, 1.0))    # on at start of period
        self.assertFalse(led.LedStatus._blink(0.6, 1.0))   # off in second half


class LatchTest(unittest.TestCase):
    def test_force_mode_latches(self):
        s = led.LedStatus()
        s.set_mode(led.PLAYING)
        self.assertEqual(s._mode, led.PLAYING)
        s.force_mode(led.SHUTDOWN)
        self.assertEqual(s._mode, led.SHUTDOWN)
        s.set_mode(led.PLAYING)            # must be ignored after latch
        self.assertEqual(s._mode, led.SHUTDOWN)


if __name__ == "__main__":
    unittest.main()
