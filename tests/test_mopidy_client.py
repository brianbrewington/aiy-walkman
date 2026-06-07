"""Mopidy HTTP JSON-RPC client: payload, result parsing, error handling."""
import json
import unittest
from contextlib import contextmanager
from unittest import mock

import _path  # noqa: F401
from walkman.mopidy_client import MopidyClient, MopidyError


@contextmanager
def fake_response(payload):
    obj = mock.Mock()
    obj.read.return_value = json.dumps(payload).encode("utf-8")
    yield obj


class MopidyClientTest(unittest.TestCase):
    def test_call_builds_request_and_returns_result(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            captured["url"] = req.full_url
            return fake_response({"jsonrpc": "2.0", "id": 1, "result": "playing"})

        m = MopidyClient(rpc_url="http://h/rpc")
        with mock.patch("walkman.mopidy_client.urllib.request.urlopen", fake_urlopen):
            self.assertEqual(m.call("core.playback.get_state"), "playing")
        self.assertEqual(captured["data"]["method"], "core.playback.get_state")
        self.assertEqual(captured["data"]["jsonrpc"], "2.0")
        self.assertEqual(captured["url"], "http://h/rpc")

    def test_call_passes_params(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return fake_response({"result": None})

        m = MopidyClient()
        with mock.patch("walkman.mopidy_client.urllib.request.urlopen", fake_urlopen):
            m.call("core.tracklist.set_random", value=True)
        self.assertEqual(captured["data"]["params"], {"value": True})

    def test_rpc_error_raises(self):
        def fake_urlopen(req, timeout=None):
            return fake_response({"error": {"message": "boom"}})

        m = MopidyClient()
        with mock.patch("walkman.mopidy_client.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(MopidyError):
                m.call("core.playback.play")

    def test_transport_error_raises(self):
        def boom(req, timeout=None):
            raise OSError("connection refused")

        m = MopidyClient()
        with mock.patch("walkman.mopidy_client.urllib.request.urlopen", boom):
            with self.assertRaises(MopidyError):
                m.call("core.get_version")
            self.assertFalse(m.is_ready())

    def test_is_ready_true(self):
        m = MopidyClient()
        with mock.patch("walkman.mopidy_client.urllib.request.urlopen",
                        lambda req, timeout=None: fake_response({"result": "4.0"})):
            self.assertTrue(m.is_ready())


if __name__ == "__main__":
    unittest.main()
