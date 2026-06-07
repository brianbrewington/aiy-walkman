#!/usr/bin/env python3
"""Walkman headphone auto-switch.

Watches the AIY bonnet's headphone-jack switch and mutes the built-in speaker when
headphones are plugged in (and un-mutes it when they're removed). The RT5645's
machine driver already powers the *headphone* output on jack-detect, but it does
NOT auto-mute the speaker — that's what this does.

Event-driven (blocks on the input device — no polling), stdlib only, runs as root
(needs to read /dev/input/event* and set the ALSA control). Lightweight enough for
the Pi Zero 2 W.

The headphone path's volume/mute and the speaker volume are left as-is; we only flip
the speaker's on/off (`Speaker Switch`) based on jack state.
"""
from __future__ import annotations

import re
import struct
import subprocess
import sys
import time

# Linux input event constants
EV_SW = 0x05
SW_HEADPHONE_INSERT = 0x02
# struct input_event on 64-bit: timeval(2x long) + type(u16) + code(u16) + value(s32)
EVENT = struct.Struct("llHHi")

CARD = "aiyvoicebonnet"      # ALSA card id (index-independent)
SPEAKER_SWITCH = "Speaker Switch"
HEADPHONE_SWITCH = "Headphone Switch"
JACK_NAME_HINTS = ("headphone jack", "voicebonnet")


def log(msg: str) -> None:
    print(f"[walkman-jack] {msg}", flush=True)


def find_jack_event_device() -> str | None:
    """Locate the headphone-jack input device via /proc/bus/input/devices."""
    try:
        data = open("/proc/bus/input/devices").read()
    except OSError:
        return None
    for block in data.split("\n\n"):
        low = block.lower()
        if all(h in low for h in JACK_NAME_HINTS):
            m = re.search(r"Handlers=.*?(event\d+)", block)
            if m:
                return "/dev/input/" + m.group(1)
    return None


def query_inserted(dev: str) -> bool:
    """Read current jack state via evtest (exit 10 = inserted, 0 = not)."""
    r = subprocess.run(
        ["evtest", "--query", dev, "EV_SW", "SW_HEADPHONE_INSERT"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return r.returncode == 10


def _switch(control: str, on: bool) -> None:
    subprocess.run(
        ["amixer", "-c", CARD, "--", "cset", f"name={control}", "on" if on else "off"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def apply(inserted: bool) -> None:
    # Drive both outputs mutually exclusively so you never hear both at once:
    #   jack inserted -> headphones only (speaker off, headphone on)
    #   jack empty    -> speaker only    (speaker on, headphone off)
    _switch(SPEAKER_SWITCH, not inserted)
    _switch(HEADPHONE_SWITCH, inserted)
    log(f"headphones {'IN' if inserted else 'OUT'} -> "
        f"speaker {'OFF' if inserted else 'ON'}, headphone {'ON' if inserted else 'OFF'}")


def main() -> int:
    dev = find_jack_event_device()
    while dev is None:
        log("jack input device not found yet; retrying in 2s")
        time.sleep(2)
        dev = find_jack_event_device()
    log(f"watching {dev} (card {CARD})")

    # set initial speaker state to match the current jack state
    apply(query_inserted(dev))

    with open(dev, "rb") as f:
        while True:
            data = f.read(EVENT.size)
            if not data or len(data) < EVENT.size:
                log("input stream ended; exiting (systemd will restart)")
                return 1
            _sec, _usec, etype, code, value = EVENT.unpack(data)
            if etype == EV_SW and code == SW_HEADPHONE_INSERT:
                apply(bool(value))


if __name__ == "__main__":
    raise SystemExit(main())
