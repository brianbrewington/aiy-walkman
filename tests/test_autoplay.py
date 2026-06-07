"""Autoplay: load playlist -> set modes -> play; bail if no tracks resolve."""
import unittest
from unittest import mock

import _path  # noqa: F401
from walkman import autoplay


def fake_mopidy(added_tracks, state="playing"):
    m = mock.MagicMock()
    def call(method, **kw):
        if method == "core.tracklist.add":
            return added_tracks
        return None
    m.call.side_effect = call
    m.get_state.return_value = state
    m.get_current_track.return_value = {"name": "Song", "artists": []}
    return m


class AutoplayTest(unittest.TestCase):
    def test_success_sets_modes_and_plays(self):
        m = fake_mopidy(added_tracks=[{"tlid": 1}, {"tlid": 2}], state="playing")
        with mock.patch("walkman.autoplay.time.sleep"):
            ok = autoplay.attempt_start(m, "yt:...", {"random": True, "repeat": True, "consume": False})
        self.assertTrue(ok)
        methods = [a[0] for a, _ in m.call.call_args_list]
        for expected in ("core.tracklist.clear", "core.tracklist.add",
                         "core.tracklist.set_random", "core.tracklist.set_repeat",
                         "core.tracklist.set_consume", "core.playback.play"):
            self.assertIn(expected, methods)

    def test_no_tracks_returns_false(self):
        m = fake_mopidy(added_tracks=[], state="stopped")
        with mock.patch("walkman.autoplay.time.sleep"):
            ok = autoplay.attempt_start(m, "yt:...", {})
        self.assertFalse(ok)
        # must not try to play if nothing loaded
        methods = [a[0] for a, _ in m.call.call_args_list]
        self.assertNotIn("core.playback.play", methods)

    def test_not_playing_returns_false(self):
        m = fake_mopidy(added_tracks=[{"tlid": 1}], state="paused")
        with mock.patch("walkman.autoplay.time.sleep"):
            ok = autoplay.attempt_start(m, "yt:...", {})
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
