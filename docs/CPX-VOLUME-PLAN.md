# Walkman — CPX Volume + VU-Meter Satellite (plan)

**Status:** CODE IMPLEMENTED · bench validation still required · 2026-06-17 · Pi
Zero 2 W, AIY Voice Bonnet, kernel 6.12.87+rpt-rpi-v8

**TL;DR.** Add an Adafruit **Circuit Playground Express** (circa 2018) on the Pi's
single USB port as an external satellite. Its **two buttons** become volume
down/up; its **10 NeoPixels** normally become a green real-time amplitude / VU
meter of the music as it plays. After a volume press, the ring shows a blue volume
level bar for 2 seconds. The CPX slide switch enables **Night mode**: no VU meter,
and only a very dim blue volume cue. One USB cable carries both power and
bidirectional serial. This realizes the "bidirectional serial satellite" hook
already designed into [`PLAN.md`](PLAN.md) §Key-decisions ("a general bidirectional
serial channel … volume input *and* a NeoPixel-ring level/VU meter driven by a
lightweight Pi-computed audio-level envelope (NOT FFT)").

This is the richer of the two volume routes. The minimal alternative — two plain
pushbuttons straight to GPIO, no CPX — is documented separately in
[`GPIO-VOLUME-BUTTONS-PLAN.md`](GPIO-VOLUME-BUTTONS-PLAN.md).

## Implementation result

Built in the repo:
- `src/walkman/satellite.py` reads CPX button events, nudges Mopidy volume, and
  streams `L:` VU levels from an ALSA loopback capture.
- `cpx/boot.py` and `cpx/code.py` implement the CircuitPython side, including the
  slide-switch Night mode.
- `setup.sh`, systemd, udev, and `config/walkman.toml` are wired for the new service.

Verified off-hardware: Python compilation, shell syntax, Pi-side unit tests, and
CPX firmware protocol/rendering tests with fake serial, fake buttons, fake slide
switch, and fake NeoPixels. Still needs bench validation on the real Pi/CPX for
`/dev/walkman-cpx`, the GStreamer tee, and glitch-free audio when
`walkman-satellite` restarts.

---

## Context

The original player had **no volume control**. [`PLAN.md`](PLAN.md) §Key-decisions
reserved a mixer capability but bound no gesture to it; the root controller still
only handles play/pause, next, and shutdown on the arcade button. Volume needs a
**new input source**, and the family also wants the lights to *do something* with
the music. The CPX gives us both in one USB-attached unit without touching the
bonnet's occupied header.

### Confirmed design decisions

- **Volume backend = Mopidy software mixer.** Mopidy runs `mixer = software`
  ([`config/mopidy.conf.example:13`](../config/mopidy.conf.example)), so
  `core.mixer.set_volume` / `get_volume` (0–100) over the existing JSON-RPC
  `MopidyClient` is the clean path: output-independent (works on speaker **or**
  headphone), persisted by `restore_state = true`, and sitting *on top of* the
  kid-safe ALSA baseline (Speaker 31 / Headphone 22 from
  [`STEP0-NOTES.md`](STEP0-NOTES.md)). No `amixer` volume juggling, no need to know
  which output the jack-switch has live.
- **Link = full bidirectional CircuitPython USB serial (CDC).** Buttons → Pi, and
  Pi-computed audio level → NeoPixels, over one cable that also powers the CPX.
- **Buttons = one volume step per press** (no hold-to-repeat). Predictable for kids.
- **NeoPixels normally show the *music's amplitude*, not the volume level.** The Pi
  computes a lightweight level envelope from the playing audio and streams it; the
  CPX renders the VU animation locally. A volume press temporarily overrides this
  with a blue proportional volume bar for 2 seconds.
- **Slide switch = Night mode.** Night mode turns the VU meter off. Volume presses
  still show a very dim blue proportional volume bar for 2 seconds, then the ring
  goes dark again.

---

## Architecture

A **self-contained satellite service**, independent of the root controller. It does
three things and talks to Mopidy directly for volume — so it needs no IPC into the
controller and can run **unprivileged** (`brew`, groups `dialout` + `audio`). It is
the 5th unit in the service split and starts **before** Mopidy so the loopback
capture side is already draining when Mopidy starts writing to the VU tap.

```
 CPX (CircuitPython)                         Pi (walkman-satellite.service)
 ───────────────────                         ──────────────────────────────
 BUTTON_A ─┐                          ┌───── serial read loop ── nudge_volume(-step)
 BUTTON_B ─┤  USB CDC (data)  <──────>┤                          via MopidyClient
 NeoPixels ┘   single cable           └───── serial write loop ◄─ audio meter loop
   (VU / blue volume / Night mode)       L:<0-255>, V:<0-100> (ALSA tap → RMS)
                                          Q / S:<logical-state> (diagnostics)
   powered over the same USB ────────────────────────────────────────────────┘
```

Volume state lives in Mopidy (single source of truth via `restore_state`), so the
satellite never has to coordinate with the controller or the GPIO route.

---

## Shared groundwork (also used by the GPIO route)

Add reusable volume helpers to **[`src/walkman/mopidy_client.py`](../src/walkman/mopidy_client.py)**,
next to the existing `get_state` / `get_current_track` convenience helpers
(`mopidy_client.py:66-71`):

```python
def get_volume(self):
    return self.call("core.mixer.get_volume")          # int 0..100, may be None

def set_volume(self, v: int) -> int:
    v = max(0, min(100, int(v)))
    self.call("core.mixer.set_volume", volume=v)
    return v

def nudge_volume(self, delta: int, lo: int = 0, hi: int = 100) -> int:
    cur = self.get_volume()
    if cur is None:
        cur = 50                                        # sane default if unknown
    return self.set_volume(max(lo, min(hi, cur + delta)))
```

And a `[volume]` section in
**[`config/walkman.toml`](../config/walkman.toml)** (shared with the GPIO route):

```toml
[volume]
step = 5      # percent per button press
min = 0
max = 70      # kid-safe ceiling
```

---

## New / changed files

| Path | What |
|------|------|
| `src/walkman/satellite.py` | the satellite process (serial + audio meter) |
| `cpx/boot.py` | enable the data CDC channel |
| `cpx/code.py` | CircuitPython: buttons → serial, serial → NeoPixel VU |
| `systemd/walkman-satellite.service` | 5th unit (user `brew`, `dialout`+`audio`) |
| `config/99-walkman-cpx.rules` | udev → stable `/dev/walkman-cpx` symlink |
| `config/modules-load-walkman-satellite.conf` | load `snd-aloop` on boot |
| `config/walkman.toml` | `[volume]` + `[satellite]` sections |
| `src/walkman/mopidy_client.py` | volume helpers (shared groundwork above) |

New dependency: **pyserial** via Debian `python3-serial` (everything else is stdlib
or existing `alsa-utils`). `setup.sh` installs it.

### `src/walkman/satellite.py` — one process, three loops

- **Serial read loop.** Find the CPX via `/dev/walkman-cpx` (udev symlink, below) or
  fall back to scanning `/dev/serial/by-id/*` for the Adafruit VID `239A`. Open the
  **data** CDC endpoint (see boot.py note), read line-framed events: `A` →
  `nudge_volume(-step)`, `B` → `nudge_volume(+step)`. Wrap each action so a
  `MopidyError` can never kill the read loop. After a successful volume change, send
  `V:<0-100>` back so the CPX can render the blue volume bar. `S:` status replies are
  logged for diagnostics and never required for normal operation.
- **Audio meter loop.** Read PCM frames from the ALSA tap (see "The hard part"),
  compute RMS with stdlib **`audioop.rms`** (C-fast; present in Python 3.11 — note
  it's deprecated in 3.13, so the implementation includes a hand-rolled fallback),
  apply smoothing, and map to a single **0–255** scalar.
- **Serial write loop.** Emit `L:<0-255>\n` at ~20–30 Hz. The CPX maps the scalar
  onto its 10-pixel ring locally, so the serial stays light.

### `cpx/boot.py` + `cpx/code.py` — CircuitPython firmware

- **`boot.py`:** `usb_cdc.enable(console=True, data=True)` so the data channel is
  separate from the REPL console. The Pi then opens the **second** ACM device — the
  `…-if02` data interface — not the console; the udev rule pins this.
- **`code.py`:**
  - Poll `board.BUTTON_A` / `board.BUTTON_B` with edge detection + debounce; emit
    exactly one `A` / `B` per press (one-step-per-press).
  - Drive `board.NEOPIXEL` (10 px) from incoming `L:` frames as a green circular VU
    meter.
  - Drive incoming `V:` frames as a blue proportional volume bar for 2 seconds.
  - Read `board.SLIDE_SWITCH` locally for Night mode: suppress the VU and show only
    very dim blue volume feedback.
  - Accept `C:` config frames for normal brightness, Night-mode volume brightness,
    and volume feedback duration.
  - Reply to `Q` with `S:<night>,<mode>,<volume>,<level>` so the Pi can verify the
    logical light state without reading raw pixels.
  - Keep the parser tolerant of partial lines (accumulate until `\n`).

### `systemd/walkman-satellite.service`

Runs `Before=walkman-mopidy.service`, `Restart=always`, `RestartSec=3`,
`User=brew`, `Group=audio`, `SupplementaryGroups=dialout`.

### `config/99-walkman-cpx.rules`

udev rule matching Adafruit's USB VID and the CircuitPython data CDC interface
number → `SYMLINK+="walkman-cpx"` with `MODE="0660" GROUP="dialout"`, so the device
path is deterministic across replug and boots.

### `config/walkman.toml` — `[satellite]`

```toml
[satellite]
device = "/dev/walkman-cpx"   # udev symlink; falls back to /dev/serial/by-id scan
level_hz = 25                 # NeoPixel level updates per second
volume_feedback_seconds = 2.0
brightness = 0.35
night_mode_volume_brightness = 0.08
smoothing = 0.4               # 0..1 envelope smoothing
audio_capture_device = "plughw:CARD=Loopback,DEV=1"
```

---

## The hard part — tapping the playing audio for the envelope ⚠️

**This remains the #1 bench-validation risk.** Mopidy must keep playing through the
bonnet exactly as today while the satellite *also* gets a copy of the samples to
measure.

### Implemented: GStreamer tee → ALSA loopback → meter

Fan Mopidy's output to **both** the real bonnet card and a capturable loopback,
without changing what the user hears:

1. Load `snd-aloop` (creates a `Loopback` card: what's played to `hw:Loopback,0`
   appears as capture on `hw:Loopback,1`).
2. `config/mopidy.conf.example` uses a GStreamer `tee` to send samples to both
   `plughw:CARD=aiyvoicebonnet` and `plughw:CARD=Loopback,DEV=0`.
3. The satellite reads the loopback **capture** side with `arecord` from
   `plughw:CARD=Loopback,DEV=1` and computes RMS on coarse chunks.

**Gotcha to document loudly:** with `snd-aloop`, if the meter stops draining the
capture side, the playback side can **stall** — i.e. a dead meter could glitch the
music. Mitigations: the meter must always read; use the loopback's non-blocking /
timer-based mode; and `Restart=always` on the unit. Validate that killing
`walkman-satellite` does **not** interrupt audio before shipping.

**CPU note:** the Zero 2 W is already loaded by yt-dlp/GStreamer (see
[`WORKLOG.md`](WORKLOG.md) §8). Keep reads coarse (~1024-frame chunks, downsample),
~20–30 RMS updates/sec — enough for a smooth VU, cheap on CPU.

### Lighter fallback (documented, not chosen): CPX-local mic VU

Render the VU **on the CPX from its onboard MEMS microphone**; serial then carries
only buttons (CPX→Pi) and optional play/pause state (Pi→CPX). This sidesteps **all**
Pi-side audio plumbing — but the mic hears the *room*, so it's useless with
headphones and picks up ambient noise. The user explicitly wants a Pi-computed
envelope over the link, so this is fallback-only (e.g. if the ALSA tee can't be made
glitch-free on this hardware).

---

## Serial protocol (ASCII, line-framed, tolerant of partial reads)

| Dir | Message | Meaning |
|-----|---------|---------|
| CPX → Pi | `A\n` | button A pressed → `nudge_volume(-step)` |
| CPX → Pi | `B\n` | button B pressed → `nudge_volume(+step)` |
| Pi → CPX | `L:<0-255>\n` | current audio level for the VU ring |
| Pi → CPX | `V:<0-100>\n` | current volume for the blue volume bar |
| Pi → CPX | `C:<brightness>,<night_mode_volume_brightness>,<seconds>\n` | CPX render settings; the second value is used only if the physical slide switch is already in Night mode |
| Pi → CPX | `Q\n` | query CPX logical light state |
| CPX → Pi | `S:<night>,<mode>,<volume>,<level>\n` | logical state; `mode` is `off`, `vu`, or `volume` |

Line-framed ASCII keeps both ends trivial and self-resynchronizing if a byte drops.
`S:` is diagnostic only; volume buttons and VU streaming continue if a status reply
is missed. The Pi logs low-rate control frames (`A`, `B`, `C:`, `Q`, `V:`, `S:`) to
journald, but suppresses the 25 Hz `L:` stream. The CPX firmware writes no persistent
logs to CIRCUITPY storage. `setup.sh` installs a journald drop-in that caps retained
logs at 64 MB / 14 days with 128 MB kept free on disk.

---

## Reused patterns / don't reinvent

- **`MopidyClient` volume helpers** (shared groundwork) — the entire volume
  mechanism; the satellite calls Mopidy directly, no controller coupling.
- **Blocking event-loop design** — mirror
  [`jack_monitor.py`](../src/walkman/jack_monitor.py): a small stdlib process that
  blocks on a device and reacts, robust to restart by systemd.
- **5th systemd unit** — copy the shape of
  [`walkman-jack.service`](../systemd/walkman-jack.service).
- **"Never let an action error kill the input thread"** — copy the guard from
  [`button.py:65-69`](../src/walkman/button.py).

---

## Gotchas / Known uncertainties

- **ALSA tee glitch-freeness is unproven on this codec/CPU** — the dominant bench
  risk; validate it first on the real Pi. A dead meter must not stall playback.
- **Which ACM is the data channel.** With `console=True, data=True` the CPX exposes
  two CDC endpoints; the Pi must open the **data** one. Pin it with the udev rule by
  interface, and verify the `…-if02` mapping on the bench.
- **`audioop` deprecation.** Stdlib in 3.11 (what the Pi runs) but removed in 3.13 —
  the implementation includes a hand-rolled RMS fallback for a future OS bump.
- **USB port is singular.** The Zero 2 W has one USB data port; the CPX consumes it
  (see [`PLAN.md`](PLAN.md) §Satellite hook). No other USB peripheral can share it
  without a hub.
- **CircuitPython on a 2018 CPX** — confirm the installed CircuitPython version
  supports `usb_cdc`; update the UF2 if needed. Pinned/bench-verified version is
  still TBD.
- **Latency.** The tee adds a buffer or two of delay between sound and lights;
  acceptable for a VU meter, but tune buffer sizes if it feels laggy.

---

## Verification (end-to-end, on the real Pi)

1. **Shared volume path:** from the Pi, `core.mixer.get_volume` /
   `set_volume` move audible volume on **both** speaker and headphone, and the level
   survives a `systemctl restart walkman-mopidy` (`restore_state`).
2. **Enumeration:** plug the CPX → `/dev/walkman-cpx` appears; the satellite service
   comes up, sends `Q`, and logs an `S:` status reply from the data channel.
3. **Buttons:** press A / B → volume steps one increment each (confirm by ear and via
   `get_volume`); holding a button does nothing extra (one-step-per-press).
4. **VU meter:** start playback → the NeoPixel ring tracks the music's amplitude in
   real time with green pixels.
5. **Night mode:** slide switch on → VU goes dark; press volume → a very dim blue
   volume bar appears for 2 seconds, then turns off.
6. **Robustness:** `systemctl stop walkman-satellite` mid-song → **audio keeps
   playing without a glitch** (the `snd-aloop` drain gotcha); `start` again → meter
   resumes.

---

## Out of scope

- No volume *gesture* on the arcade button (it stays full: single/double/long).
- Repeat-track and now-playing OLED stay in [`IDEAS.md`](IDEAS.md) — though both are
  natural future passengers on this same serial link once it exists.
- The standalone GPIO-button route lives in
  [`GPIO-VOLUME-BUTTONS-PLAN.md`](GPIO-VOLUME-BUTTONS-PLAN.md).
