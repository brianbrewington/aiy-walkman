#!/usr/bin/env python3
"""Patch Mopidy-YouTube's one unguarded ``result["author"]``.

Background: ``youtube_music.py`` builds a playlist's top-level ``artists`` with

    result["artists"] = [result["author"]]

When ``ytmusicapi.get_playlist()`` returns a playlist whose result has no
``author`` key (seen with a kid's playlist that contained a plain YouTube video,
not a "song"), this raises ``KeyError: 'author'`` and kills the **entire**
playlist load -> 0 tracks -> autoplay fails. The per-track parser
(``ytm_item_to_video``) already tolerates a missing author; only this
playlist-level line didn't. We guard it:

    result["artists"] = [result["author"]] if result.get("author") else []

This module is the single source of truth for that patch: ``setup.sh`` runs it on
install (so every unit/reinstall gets the fix), and ``tests/`` import
``patch_text`` to guard against regressing the transformation. Idempotent.
"""
import os
import sys

OLD = 'result["artists"] = [result["author"]]'
NEW = 'result["artists"] = [result["author"]] if result.get("author") else []'


def patch_text(s):
    """Return (new_text, status). status in {applied, already, notfound}."""
    if NEW in s:
        return s, "already"
    if s.count(OLD) == 1:
        return s.replace(OLD, NEW, 1), "applied"
    return s, "notfound"


def _target_path():
    """Locate the installed mopidy_youtube/apis/youtube_music.py, or None."""
    try:
        import mopidy_youtube
    except Exception:
        return None
    return os.path.join(os.path.dirname(mopidy_youtube.__file__),
                        "apis", "youtube_music.py")


def main(argv):
    path = argv[1] if len(argv) > 1 else _target_path()
    if not path or not os.path.exists(path):
        print("WARNING: mopidy_youtube not found; skipping author-guard patch")
        return 0  # not fatal for setup.sh — pip step already warns separately
    with open(path) as f:
        s = f.read()
    new, status = patch_text(s)
    if status == "applied":
        with open(path, "w") as f:
            f.write(new)
        # drop stale bytecode so the patched source is what loads
        import pathlib
        for pyc in pathlib.Path(os.path.dirname(path)).rglob("*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass
        print(f"mopidy-youtube author guard applied: {path}")
    elif status == "already":
        print("mopidy-youtube author guard already applied")
    else:
        print(f"WARNING: author line not found as expected in {path}; "
              "upstream changed — review apis/youtube_music.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
