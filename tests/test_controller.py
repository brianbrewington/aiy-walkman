"""Controller gesture dispatch + actions (with Mopidy/subprocess/LED mocked)."""
import unittest
from unittest import mock

import _path  # noqa: F401
from walkman import led
from walkman.controller import Controller, decide_mode


class DecideModeTest(unittest.TestCase):
    def test_missing_auth_is_magenta(self):
        self.assertEqual(decide_mode(False, True, True, "playing", 0), led.REAUTH)

    def test_mopidy_startup_then_error(self):
        self.assertEqual(decide_mode(True, False, True, None, 1), led.STARTUP)
        self.assertEqual(decide_mode(True, False, True, None, 999), led.ERROR)

    def test_wifi_down_is_blue(self):
        self.assertEqual(decide_mode(True, True, False, "playing", 0), led.STARTUP)

    def test_playing_and_paused(self):
        self.assertEqual(decide_mode(True, True, True, "playing", 0), led.PLAYING)
        self.assertEqual(decide_mode(True, True, True, "paused", 0), led.PAUSED)
        self.assertEqual(decide_mode(True, True, True, None, 0), led.PAUSED)

CFG = {"mopidy": {"rpc_url": "http://x/rpc"}, "button": {}, "led": {}}


def make_controller(state="playing"):
    c = Controller(dict(CFG))
    c.mopidy = mock.MagicMock()
    c.mopidy.get_state.return_value = state
    c.led = mock.MagicMock()
    return c


class StartupShutdownTest(unittest.TestCase):
    def test_start_keeps_strong_ref_to_button_source(self):
        # regression: gpiozero closes a Button that gets GC'd -> button input dies
        c = make_controller("playing")
        with mock.patch("walkman.controller.ButtonSource") as BS, \
             mock.patch("walkman.controller.signal.pause"), \
             mock.patch("walkman.controller.threading.Thread"):
            c.start()
        self.assertIs(c._button_source, BS.return_value)

    def test_gestures_ignored_while_shutting_down(self):
        c = make_controller("playing")
        c._shutting_down = True
        c.handle_gesture("single")
        c.handle_gesture("double")
        c.mopidy.call.assert_not_called()


class GestureTest(unittest.TestCase):
    def test_single_when_playing_pauses(self):
        c = make_controller("playing")
        c.handle_gesture("single")
        c.mopidy.call.assert_any_call("core.playback.pause")

    def test_single_when_paused_plays(self):
        c = make_controller("paused")
        c.handle_gesture("single")
        c.mopidy.call.assert_any_call("core.playback.play")

    def test_double_advances_and_resumes(self):
        # regression: next() on a paused player stays paused -> silence; we must also play
        c = make_controller("paused")
        c.handle_gesture("double")
        methods = [args[0] for args, _ in c.mopidy.call.call_args_list]
        self.assertIn("core.playback.next", methods)
        self.assertIn("core.playback.play", methods)
        self.assertLess(methods.index("core.playback.next"),
                        methods.index("core.playback.play"))

    def test_long_latches_white_and_powers_off(self):
        c = make_controller("playing")
        with mock.patch("walkman.controller.subprocess") as sp:
            sp.run.return_value.returncode = 0   # poweroff succeeds
            c.handle_gesture("long")
        c.led.force_mode.assert_called_once_with(led.SHUTDOWN)   # latched, not set_mode
        self.assertTrue(c._shutting_down)
        calls = [args[0] for args, _ in sp.run.call_args_list]
        self.assertIn(["systemctl", "poweroff"], calls)

    def test_long_press_recovers_if_poweroff_fails(self):
        # if poweroff fails/hangs, don't leave the device wedged under root
        c = make_controller("playing")
        with mock.patch("walkman.controller.subprocess") as sp:
            sp.run.return_value.returncode = 1   # poweroff fails
            c.handle_gesture("long")
        self.assertFalse(c._shutting_down)        # shutdown flag cleared -> gestures live
        c.led.release_force.assert_called_once()  # white latch released

    def test_action_error_does_not_crash(self):
        from walkman.mopidy_client import MopidyError
        c = make_controller("playing")
        c.mopidy.get_state.side_effect = MopidyError("down")
        c.handle_gesture("single")  # should swallow the error, not raise


if __name__ == "__main__":
    unittest.main()
