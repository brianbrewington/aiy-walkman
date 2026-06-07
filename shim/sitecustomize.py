"""Walkman yt-dlp shim — force YouTube's android player client.

Loaded into the Mopidy process via PYTHONPATH (see walkman-mopidy.service) so we
DON'T edit the Mopidy-YouTube package. Python auto-imports `sitecustomize` at
interpreter startup when it's on sys.path.

Why: YouTube's default (web) client requires JS *signature/nsig solving*, which
needs a JS runtime (deno) + the EJS solver — and that path segfaults inside
Mopidy's threaded/GStreamer context. The `android_vr`/`ios` clients return
*unsigned* stream URLs, so no JS-solving happens at all. This makes track
switching / `next` work reliably and avoids the segfault.

Fully reversible: remove the PYTHONPATH entry (or this file) to undo.
"""
import sys

# android_vr: fast, no JS-solving (works for many tracks). web: deno signature-
# solving fallback (slower, but resolves tracks android can't). Together = best
# coverage on the Pi Zero 2 W without a PO-token provider. See docs/STEP3-NOTES.md.
PLAYER_CLIENTS = ["android_vr", "web"]


def _install() -> None:
    try:
        import yt_dlp
    except Exception:
        return
    if getattr(yt_dlp.YoutubeDL, "_walkman_android_patch", False):
        return

    _orig_init = yt_dlp.YoutubeDL.__init__

    def __init__(self, params=None, *args, **kwargs):
        params = dict(params or {})
        extractor_args = dict(params.get("extractor_args") or {})
        youtube = dict(extractor_args.get("youtube") or {})
        youtube.setdefault("player_client", list(PLAYER_CLIENTS))
        extractor_args["youtube"] = youtube
        params["extractor_args"] = extractor_args
        _orig_init(self, params, *args, **kwargs)

    yt_dlp.YoutubeDL.__init__ = __init__
    yt_dlp.YoutubeDL._walkman_android_patch = True
    print(f"[walkman] yt-dlp player_client forced to {PLAYER_CLIENTS}", file=sys.stderr, flush=True)


_install()
