"""Walkman controller: button gestures -> player actions.

The long-running control process. Step 3 wires the one button's gestures to Mopidy
actions (and a safe shutdown). Step 4 will add the player-state -> LED status loop to
this same process (the plan folds button + LED + Mopidy control into one controller
to keep things light on the Pi Zero 2 W).

Gestures:
  single -> play/pause toggle
  double -> next track
  long   -> safe shutdown (LED cue, then clean power-off)

Runs as root (GPIO + LED sysfs + poweroff). Mopidy is reached over HTTP JSON-RPC.

Usage: python3 controller.py [/path/to/walkman.toml]
"""
from __future__ import annotations

import signal
import subprocess
import sys
import tomllib
from pathlib import Path

try:
    from walkman.mopidy_client import MopidyClient, MopidyError
    from walkman import led
    from walkman.button import ButtonSource
except ImportError:  # allow running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from walkman.mopidy_client import MopidyClient, MopidyError
    from walkman import led
    from walkman.button import ButtonSource

DEFAULT_CONFIG = "/home/brew/walkman/config/walkman.toml"


def log(msg: str) -> None:
    print(f"[walkman-controller] {msg}", flush=True)


class Controller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        rpc = cfg.get("mopidy", {}).get("rpc_url", "http://127.0.0.1:6680/mopidy/rpc")
        self.mopidy = MopidyClient(rpc_url=rpc, timeout=10.0)
        self.button_cfg = cfg.get("button", {})
        self._source: ButtonSource | None = None

    # --- actions ---
    def handle_gesture(self, gesture: str) -> None:
        log(f"gesture: {gesture}")
        if gesture == "single":
            self.play_pause()
        elif gesture == "double":
            self.next_track()
        elif gesture == "long":
            self.safe_shutdown()

    def play_pause(self) -> None:
        try:
            state = self.mopidy.get_state()
            if state == "playing":
                self.mopidy.call("core.playback.pause")
                log("-> paused")
            else:
                self.mopidy.call("core.playback.play")
                log("-> playing")
        except MopidyError as e:
            log(f"play_pause failed: {e}")

    def next_track(self) -> None:
        try:
            self.mopidy.call("core.playback.next")
            # Ensure the next track is actually audible: next() on a *paused*
            # player advances the track but stays paused (-> silence). Pressing
            # "next" should always result in music playing.
            self.mopidy.call("core.playback.play")
            log("-> next track")
        except MopidyError as e:
            log(f"next failed: {e}")

    def safe_shutdown(self) -> None:
        log("LONG PRESS -> safe shutdown")
        try:
            led.shutdown_cue()  # solid white = "shutting down, safe to unplug soon"
        except Exception as e:
            log(f"led cue failed: {e}")
        subprocess.run(["sync"])
        subprocess.run(["systemctl", "poweroff"])

    # --- lifecycle ---
    def start(self) -> None:
        self._source = ButtonSource(
            pin=int(self.button_cfg.get("gpio", 23)),
            on_gesture=self.handle_gesture,
            long_press=float(self.button_cfg.get("long_press_seconds", 1.2)),
            double_window=float(self.button_cfg.get("double_click_window_seconds", 0.35)),
            log=log,
        )
        log("controller running; waiting for button presses")
        signal.pause()


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    Controller(load_config(config_path)).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
