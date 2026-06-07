"""KTD2026 button-LED control via the bound kernel driver (/sys/class/leds).

Channel→color map (pinned in Step 0): led1=red, led2=green, led3=blue (led4 unused).
Brightness is 0–255 per channel. Writing the sysfs `brightness` attribute requires
root (the Walkman controller runs as root).

Step 3 only needs simple solid colors (the shutdown cue). Step 4 will extend this
module with the breathing/blink effects and the player-state→color loop.
"""
from __future__ import annotations

_BRIGHTNESS = "/sys/class/leds/ktd202x:led{n}/brightness"
_CHANNEL = {"r": 1, "g": 2, "b": 3}

# A few named colors (0–255 per channel)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
AMBER = (255, 90, 0)
MAGENTA = (255, 0, 255)
WHITE = (255, 255, 255)
OFF = (0, 0, 0)


def _write(channel: int, value: int) -> None:
    try:
        with open(_BRIGHTNESS.format(n=channel), "w") as f:
            f.write(str(max(0, min(255, int(value)))))
    except OSError:
        pass  # fail graceful: never crash the controller over the LED


def set_rgb(r: int, g: int, b: int) -> None:
    _write(_CHANNEL["r"], r)
    _write(_CHANNEL["g"], g)
    _write(_CHANNEL["b"], b)


def set_color(color: tuple[int, int, int]) -> None:
    set_rgb(*color)


def off() -> None:
    set_rgb(0, 0, 0)


def shutdown_cue() -> None:
    """Distinct 'shutting down' indication: solid white (held until power-off)."""
    set_color(WHITE)


def available() -> bool:
    import os
    return os.path.exists(_BRIGHTNESS.format(n=1))
