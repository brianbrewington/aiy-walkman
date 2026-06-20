#!/usr/bin/env python3
"""Interactive sync-delay tuner for the CPX meter.

Run over SSH while music plays. Arrow keys nudge meter_sync_delay_ms LIVE — the running
walkman-satellite picks it up within a frame via the tuning file (config key
`meter_tuning_file`). Watch the ring flash against the audio (a 40 BPM 3:2 polyrhythm
probe is unambiguous) and dial it in. Press 'w' to persist the value into walkman.toml.
'q' quits and removes the override file so the saved value takes over.

  ssh brew@walkman-b.local
  python3 ~/walkman/scripts/walkman-meter-tune.py

Stdlib only. Resolution is one meter frame (1000/level_hz ms; 40 ms at 25 Hz).
"""
import argparse
import curses
import os
import re
import tomllib

DEFAULT_CONFIG = "/home/brew/walkman/config/walkman.toml"


def load(path):
    with open(path, "rb") as f:
        sat = tomllib.load(f).get("satellite", {})
    return {
        "tuning_file": str(sat.get("meter_tuning_file", "")),
        "delay_ms": int(sat.get("meter_sync_delay_ms", 0)),
        "level_hz": int(sat.get("level_hz", 25)),
    }


def write_toml_delay(path, ms):
    """Replace the meter_sync_delay_ms value in-place. Returns True if the key was found."""
    with open(path) as f:
        text = f.read()
    new, n = re.subn(r"(?m)^(meter_sync_delay_ms\s*=\s*)\d+",
                     lambda m: m.group(1) + str(ms), text)
    if n == 0:
        return False
    with open(path, "w") as f:
        f.write(new)
    return True


def _tui(stdscr, cfg, config_path):
    curses.curs_set(0)
    stdscr.keypad(True)
    frame_ms = max(1, round(1000 / cfg["level_hz"]))
    ms = cfg["delay_ms"]
    tuning_file = cfg["tuning_file"]
    status = ""

    def push():
        if not tuning_file:
            return "no meter_tuning_file in config — set it + restart the satellite"
        try:
            with open(tuning_file, "w") as f:
                f.write(str(ms))
            return "live"
        except OSError as e:
            return f"override write failed: {e}"

    status = push()
    while True:
        frames = round(ms / frame_ms)
        stdscr.erase()
        stdscr.addstr(0, 0, "Walkman — CPX meter sync-delay tuner")
        stdscr.addstr(2, 2, f"delay = {ms:4d} ms   ({frames} frames @ {cfg['level_hz']}Hz, {frame_ms}ms/frame)")
        stdscr.addstr(3, 2, f"tuning file: {tuning_file or '(unset!)'}   [{status}]")
        stdscr.addstr(5, 2, "up / down   +/- 1 frame")
        stdscr.addstr(6, 2, "PgUp / PgDn +/- 5 frames")
        stdscr.addstr(7, 2, "0           reset to 0")
        stdscr.addstr(8, 2, "w           save to walkman.toml")
        stdscr.addstr(9, 2, "q           quit (revert to saved)")
        stdscr.refresh()
        k = stdscr.getch()
        if k == curses.KEY_UP:
            ms += frame_ms
        elif k == curses.KEY_DOWN:
            ms = max(0, ms - frame_ms)
        elif k == curses.KEY_PPAGE:
            ms += 5 * frame_ms
        elif k == curses.KEY_NPAGE:
            ms = max(0, ms - 5 * frame_ms)
        elif k == ord("0"):
            ms = 0
        elif k == ord("w"):
            status = "SAVED to walkman.toml" if write_toml_delay(config_path, ms) \
                else "save FAILED (meter_sync_delay_ms not found)"
            push()
            continue
        elif k in (ord("q"), 27):
            break
        else:
            continue
        status = push()

    # revert: remove the override so the satellite falls back to the toml value
    if tuning_file:
        try:
            os.remove(tuning_file)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="Live-tune the CPX meter sync delay.")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()
    curses.wrapper(_tui, load(args.config), args.config)


if __name__ == "__main__":
    main()
