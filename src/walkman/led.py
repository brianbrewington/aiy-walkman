"""KTD2026 button-LED control via the bound kernel driver (/sys/class/leds).

Channel→color map (pinned in Step 0): led1=red, led2=green, led3=blue (led4 unused).
Brightness is 0–255 per channel. Writing the sysfs `brightness` attribute requires
root (the Walkman controller runs as root).

Provides:
- low-level set_rgb/set_color/off
- `LedStatus`: a lightweight background thread that renders the player-state →
  LED effect (green breathing / amber steady / blue·red·magenta blink / white).
  One render loop (~30 fps) that only writes sysfs when the output changes, so
  steady/blink modes are nearly free and breathing is still smooth on the Pi Zero.
"""
from __future__ import annotations

import math
import threading
import time

_BRIGHTNESS = "/sys/class/leds/ktd202x:led{n}/brightness"
_CH_RED, _CH_GREEN, _CH_BLUE = 1, 2, 3

# Named colors (0–255 per channel), tuned calm.
RED = (180, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 180)
AMBER = (180, 60, 0)
MAGENTA = (160, 0, 160)
WHITE = (200, 200, 200)
OFF = (0, 0, 0)


def _write(channel: int, value: int) -> None:
    try:
        with open(_BRIGHTNESS.format(n=channel), "w") as f:
            f.write(str(max(0, min(255, int(value)))))
    except OSError:
        pass  # fail graceful: never crash the controller over the LED


def set_rgb(r: int, g: int, b: int) -> None:
    _write(_CH_RED, r)
    _write(_CH_GREEN, g)
    _write(_CH_BLUE, b)


def set_color(color) -> None:
    set_rgb(*color)


def off() -> None:
    set_rgb(0, 0, 0)


def available() -> bool:
    import os
    return os.path.exists(_BRIGHTNESS.format(n=1))


# --- status effect engine -------------------------------------------------

# Modes the controller can request:
PLAYING = "playing"      # green, gentle breathing
PAUSED = "paused"        # amber, steady
STARTUP = "startup"      # blue, blinking (booting / wifi down / mopidy not ready)
ERROR = "error"          # red, blinking (mopidy unreachable after retries)
REAUTH = "reauth"        # magenta, slow blink (YT cookie expired — needs re-login)
SHUTDOWN = "shutdown"    # white, steady (held until power-off)
IDLE = "idle"            # off


def _scale(color, factor: float):
    return tuple(int(c * factor) for c in color)


class LedStatus(threading.Thread):
    """Renders the current mode to the button LED until stopped."""

    def __init__(self, max_brightness: float = 0.65, breathe_period_s: float = 4.0,
                 log=print):
        super().__init__(daemon=True)
        self.max_brightness = max_brightness          # global dimmer (calm)
        self.breathe_period_s = breathe_period_s
        self.log = log
        self._mode = STARTUP
        self._latched = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_rgb = None

    def set_mode(self, mode: str) -> None:
        with self._lock:
            if self._latched:
                return  # a latched mode (e.g. shutdown) wins over status updates
            if mode != self._mode:
                self._mode = mode

    def force_mode(self, mode: str) -> None:
        """Set a mode that can't be overridden by later set_mode calls.

        Used for the shutdown cue: otherwise an in-flight status-poller tick can
        race and flip the LED back (white flashes, then reverts). Latching keeps
        white solid until power cuts.
        """
        with self._lock:
            self._mode = mode
            self._latched = True

    def stop(self) -> None:
        self._stop.set()

    # square-wave blink helper: returns True during the "on" phase
    @staticmethod
    def _blink(t: float, period: float, duty: float = 0.5) -> bool:
        return (t % period) < (period * duty)

    def _render(self, mode: str, t: float):
        if mode == PLAYING:
            # green breathing: smooth sine between ~12% and 100% of max
            phase = 0.5 * (1 + math.sin(2 * math.pi * t / self.breathe_period_s - math.pi / 2))
            level = 0.12 + 0.88 * phase
            return (0, int(255 * level * self.max_brightness), 0)
        if mode == PAUSED:
            return _scale(AMBER, self.max_brightness)
        if mode == STARTUP:
            return _scale(BLUE, self.max_brightness) if self._blink(t, 1.0) else OFF
        if mode == ERROR:
            return _scale(RED, self.max_brightness) if self._blink(t, 0.6) else OFF
        if mode == REAUTH:
            return _scale(MAGENTA, self.max_brightness) if self._blink(t, 1.6, 0.5) else OFF
        if mode == SHUTDOWN:
            return WHITE  # full white, distinct + clearly "about to power off"
        return OFF  # IDLE / unknown

    def run(self) -> None:
        t0 = time.monotonic()
        while not self._stop.is_set():
            with self._lock:
                mode = self._mode
            rgb = self._render(mode, time.monotonic() - t0)
            if rgb != self._last_rgb:
                set_rgb(*rgb)
                self._last_rgb = rgb
            time.sleep(0.03)
        off()
