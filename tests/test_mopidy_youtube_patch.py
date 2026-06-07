"""Regression guard for the Mopidy-YouTube author-KeyError patch.

mopidy_youtube isn't a repo dependency (it's pip-installed only on the Pi), so we
can't import it here. Instead we guard the patch itself: the transformation, its
idempotency, and — most importantly — that the *patched* expression actually
tolerates a playlist whose result has no "author" key (the real bug: a kid's
playlist with a non-song video made ytmusicapi omit "author", and the original
unguarded line crashed the whole playlist load).
"""
import unittest

import _path  # noqa: F401
from patch_mopidy_youtube import OLD, NEW, patch_text


def _artists(expr, result):
    """Evaluate the assignment expr against a given `result` dict; return artists."""
    scope = {"result": dict(result)}
    exec(expr, {}, scope)
    return scope["result"]["artists"]


class PatchTransformTest(unittest.TestCase):
    def test_applies_to_unguarded_line(self):
        src = "    " + OLD + "\n"
        out, status = patch_text(src)
        self.assertEqual(status, "applied")
        self.assertIn(NEW, out)
        self.assertNotIn(OLD + "\n", out)  # the bare unguarded form is gone

    def test_idempotent(self):
        once, _ = patch_text("    " + OLD + "\n")
        twice, status = patch_text(once)
        self.assertEqual(status, "already")
        self.assertEqual(once, twice)

    def test_notfound_when_upstream_changed(self):
        out, status = patch_text("something entirely different\n")
        self.assertEqual(status, "notfound")
        self.assertEqual(out, "something entirely different\n")


class PatchedBehaviorTest(unittest.TestCase):
    def test_old_line_crashes_without_author(self):
        # the bug: the original line KeyErrors when "author" is absent
        with self.assertRaises(KeyError):
            _artists(OLD, {"id": "PL123"})

    def test_new_line_survives_missing_author(self):
        self.assertEqual(_artists(NEW, {"id": "PL123"}), [])

    def test_new_line_preserves_author_when_present(self):
        author = {"name": "Some Artist", "id": "UC1"}
        self.assertEqual(_artists(NEW, {"id": "PL123", "author": author}), [author])


if __name__ == "__main__":
    unittest.main()
