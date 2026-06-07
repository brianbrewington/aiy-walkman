"""ytmusic_auth_from_curl: cURL/cookies.txt parsing + SAPISIDHASH building."""
import re
import unittest

import _path  # noqa: F401
import ytmusic_auth_from_curl as conv


class SapisidTest(unittest.TestCase):
    def test_extract_sapisid(self):
        ck = "VISITOR=1; SAPISID=abc123; __Secure-3PAPISID=xyz"
        self.assertEqual(conv.sapisid_from_cookie(ck), "abc123")

    def test_fallback_to_secure_3papisid(self):
        ck = "VISITOR=1; __Secure-3PAPISID=onlythis"
        self.assertEqual(conv.sapisid_from_cookie(ck), "onlythis")

    def test_get_authorization_format(self):
        auth = conv.get_authorization("SAP https://music.youtube.com")
        self.assertTrue(auth.startswith("SAPISIDHASH "))
        self.assertRegex(auth, r"^SAPISIDHASH \d+_[0-9a-f]{40}$")


class ParseCurlTest(unittest.TestCase):
    def test_headers_and_cookie(self):
        curl = (
            "curl 'https://music.youtube.com/youtubei/v1/browse' "
            "-H 'cookie: VISITOR=1; SAPISID=abc; __Secure-3PAPISID=xyz' "
            "-H 'user-agent: TestUA'"
        )
        h = conv.parse_curl(curl)
        self.assertIn("cookie", {k.lower() for k in h})
        self.assertEqual(h.get("user-agent"), "TestUA")

    def test_drops_stale_authorization_and_encoding(self):
        curl = (
            "curl x -H 'cookie: SAPISID=abc' "
            "-H 'authorization: SAPISIDHASH 123_deadbeef' "
            "-H 'accept-encoding: gzip, br'"
        )
        h = {k.lower() for k in conv.parse_curl(curl)}
        self.assertIn("cookie", h)
        self.assertNotIn("authorization", h)      # stale -> dropped (ytmusicapi recomputes)
        self.assertNotIn("accept-encoding", h)     # avoid br/zstd

    def test_ansi_c_quoting(self):
        # bash $'...' style that an earlier parser version missed
        curl = "curl x -H $'cookie: SAPISID=abc'"
        self.assertIn("cookie", {k.lower() for k in conv.parse_curl(curl)})


class ParseCookiesTxtTest(unittest.TestCase):
    def test_netscape(self):
        txt = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tabc123\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\t__Secure-3PAPISID\txyz\n"
        )
        h = conv.parse_cookies_txt(txt)
        cookie = h["cookie"]
        self.assertIn("SAPISID=abc123", cookie)
        self.assertIn("__Secure-3PAPISID=xyz", cookie)
        self.assertIn("user-agent", h)  # base headers added

    def test_empty_when_no_youtube_cookies(self):
        self.assertEqual(conv.parse_cookies_txt("# Netscape\n.example.com\tTRUE\t/\tTRUE\t0\tX\tY\n"), {})


if __name__ == "__main__":
    unittest.main()
