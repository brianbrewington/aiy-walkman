"""Walkman autoplay-on-boot.

Run once at boot (after Mopidy is up): wait for Mopidy + network, load the
configured YouTube Music playlist, enable shuffle/repeat, and start playback —
so the device powers on straight into music with zero interaction.

Tolerates Mopidy-not-ready-yet and transient network/auth failures by retrying
with backoff. Logs to stdout (journald). Exits 0 on success, non-zero if it
couldn't start playback after all retries.

Usage: python3 -m walkman.autoplay [/path/to/walkman.toml]
"""
from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path

try:
    from walkman.mopidy_client import MopidyClient, MopidyError
except ImportError:  # allow running as a plain script (systemd ExecStart)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from walkman.mopidy_client import MopidyClient, MopidyError

DEFAULT_CONFIG = "/home/brew/walkman/config/walkman.toml"
PLAYLIST_URI_TMPL = "yt:https://music.youtube.com/playlist?list={id}"


def log(msg: str) -> None:
    print(f"[walkman-autoplay] {msg}", flush=True)


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def attempt_start(m: MopidyClient, playlist_uri: str, playback: dict) -> bool:
    """One full load+play attempt. Returns True if playback is confirmed playing."""
    m.call("core.playback.stop")
    m.call("core.tracklist.clear")
    added = m.call("core.tracklist.add", uris=[playlist_uri]) or []
    log(f"loaded {len(added)} tracks from playlist")
    if not added:
        return False
    m.call("core.tracklist.set_random", value=bool(playback.get("random", True)))
    m.call("core.tracklist.set_repeat", value=bool(playback.get("repeat", True)))
    m.call("core.tracklist.set_consume", value=bool(playback.get("consume", False)))
    m.call("core.playback.play")
    # confirm it actually started
    for _ in range(5):
        time.sleep(2)
        if m.get_state() == "playing":
            track = m.get_current_track() or {}
            artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
            log(f"playing: {track.get('name')} — {artists}")
            return True
    return False


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_config(config_path)
    playlist_id = cfg["playlist"]["id"]
    playback = cfg.get("playback", {})
    rpc_url = cfg.get("mopidy", {}).get("rpc_url", "http://127.0.0.1:6680/mopidy/rpc")
    playlist_uri = PLAYLIST_URI_TMPL.format(id=playlist_id)

    # generous timeout — the playlist `add` resolves tracks via ytmusicapi/yt-dlp
    m = MopidyClient(rpc_url=rpc_url, timeout=120.0)

    log(f"config={config_path} playlist={playlist_id}")
    if not m.wait_until_ready(timeout=180.0, log=log):
        log("ERROR: Mopidy never became ready")
        return 2

    # Retry the load+play with backoff (covers wifi/yt-dlp warm-up).
    delays = [0, 5, 10, 20, 30]
    for i, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            if attempt_start(m, playlist_uri, playback):
                log("autoplay OK")
                return 0
            log(f"attempt {i}/{len(delays)} did not start playback; retrying")
        except MopidyError as e:
            log(f"attempt {i}/{len(delays)} error: {e}")
    log("ERROR: could not start playback after retries")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
