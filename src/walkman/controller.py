"""Walkman controller: the one long-running control process.

Folds the three responsibilities into one process (light on the Pi Zero 2 W):
  - button gestures  -> Mopidy actions
  - player/system state -> button LED status
  - talking to Mopidy over HTTP JSON-RPC

Gestures:
  single -> play/pause toggle
  double -> next track (also resumes if paused)
  long   -> safe shutdown (white LED, then clean power-off)

LED status (see led.LedStatus):
  playing -> green breathing | paused -> amber steady
  startup / wifi-down / mopidy-not-ready -> blue blink
  mopidy unreachable after retries -> red blink
  (re-auth needed -> magenta blink — reserved; auto-detection TBD with cookie refresh)
  shutting down -> white

Runs as root (GPIO + LED sysfs + poweroff). Usage: controller.py [walkman.toml]
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
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
ERROR_AFTER_S = 30.0       # mopidy unreachable this long -> red (vs blue startup)
WIFI_CHECK_EVERY = 3       # seconds between wifi checks


def log(msg: str) -> None:
    print(f"[walkman-controller] {msg}", flush=True)


def decide_mode(auth_exists: bool, mopidy_ready: bool, wifi_ok: bool,
                state, unreachable_seconds: float, error_after_s: float = ERROR_AFTER_S) -> str:
    """Pure player/system state -> LED mode (unit-tested)."""
    if not auth_exists:
        return led.REAUTH                      # no YT auth file -> "set me up" (magenta)
    if not mopidy_ready:
        return led.ERROR if unreachable_seconds >= error_after_s else led.STARTUP
    if not wifi_ok:
        return led.STARTUP                     # blue: wifi/internet down (recovers)
    return led.PLAYING if state == "playing" else led.PAUSED


class Controller:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        rpc = cfg.get("mopidy", {}).get("rpc_url", "http://127.0.0.1:6680/mopidy/rpc")
        self.mopidy = MopidyClient(rpc_url=rpc, timeout=8.0)
        self.button_cfg = cfg.get("button", {})
        self.auth_file = cfg.get("auth", {}).get(
            "file", "/home/brew/.config/walkman/ytmusic-auth.json")
        led_cfg = cfg.get("led", {})
        self.led = led.LedStatus(
            max_brightness=float(led_cfg.get("max_brightness", 0.65)),
            breathe_period_s=float(led_cfg.get("breathe_period_seconds", 4.0)),
            log=log,
        )
        self._stop = threading.Event()
        self._shutting_down = False

    # --- actions ---
    def handle_gesture(self, gesture: str) -> None:
        if self._shutting_down:
            return  # shutdown is underway/latched — ignore further button input
        log(f"gesture: {gesture}")
        if gesture == "single":
            self.play_pause()
        elif gesture == "double":
            self.next_track()
        elif gesture == "long":
            self.safe_shutdown()

    def play_pause(self) -> None:
        try:
            if self.mopidy.get_state() == "playing":
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
            # next() on a *paused* player stays paused (-> silence); always play.
            self.mopidy.call("core.playback.play")
            log("-> next track")
        except MopidyError as e:
            log(f"next failed: {e}")

    def safe_shutdown(self) -> None:
        log("LONG PRESS -> safe shutdown")
        self._shutting_down = True
        self.led.force_mode(led.SHUTDOWN)  # latch white so the poller can't override it
        time.sleep(0.3)                   # let the LED latch white first
        subprocess.run(["sync"])
        try:
            r = subprocess.run(["systemctl", "poweroff"], timeout=30)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl poweroff exited {r.returncode}")
            # success: the box is powering off; this process dies with it.
        except Exception as e:
            # poweroff failed or hung — don't leave the device wedged (white LED
            # latched, gestures disabled) under root. Recover so it stays operable.
            log(f"poweroff failed ({e}); staying up and re-enabling control")
            self._shutting_down = False
            self.led.release_force()

    # --- LED status poller ---
    @staticmethod
    def _wifi_ok() -> bool:
        """Internet reachability via NetworkManager (cached). Fail-open."""
        try:
            r = subprocess.run(["nmcli", "-t", "networking", "connectivity"],
                               capture_output=True, text=True, timeout=4)
            state = r.stdout.strip()
            if state in ("none", "limited", "portal"):
                return False
            return True  # "full" or "unknown" -> don't false-alarm
        except Exception:
            return True

    def _status_loop(self) -> None:
        unreachable_since = None
        wifi_ok = True
        tick = 0
        while not self._stop.is_set():
            if self._shutting_down:
                self._stop.wait(1.0)
                continue
            tick += 1
            if tick % WIFI_CHECK_EVERY == 1:
                wifi_ok = self._wifi_ok()

            ready = self.mopidy.is_ready()
            now = time.monotonic()
            if not ready:
                if unreachable_since is None:
                    unreachable_since = now
                unreachable_seconds = now - unreachable_since
            else:
                unreachable_since = None
                unreachable_seconds = 0.0

            state = None
            if ready:
                try:
                    state = self.mopidy.get_state()
                except MopidyError:
                    state = None

            mode = decide_mode(os.path.exists(self.auth_file), ready, wifi_ok,
                               state, unreachable_seconds)
            self.led.set_mode(mode)
            self._stop.wait(1.0)

    # --- lifecycle ---
    def start(self) -> None:
        self.led.start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        # Keep a strong reference: gpiozero closes a Button that gets garbage-collected,
        # which would silently kill all button input (see docs/GPIO-VOLUME-BUTTONS-PLAN.md).
        self._button_source = ButtonSource(
            pin=int(self.button_cfg.get("gpio", 23)),
            on_gesture=self.handle_gesture,
            long_press=float(self.button_cfg.get("long_press_seconds", 1.2)),
            double_window=float(self.button_cfg.get("double_click_window_seconds", 0.35)),
            log=log,
        )
        log("controller running; button + LED status active")
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
