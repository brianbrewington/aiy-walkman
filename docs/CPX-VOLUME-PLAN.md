# Walkman — CPX Volume + VU-Meter Satellite (plan)

**Status:** PLANNED · not built · 2026-06-07 · Pi Zero 2 W, AIY Voice Bonnet,
kernel 6.12.87+rpt-rpi-v8

**TL;DR.** Add an Adafruit **Circuit Playground Express** (circa 2018) on the Pi's
single USB port as an external satellite. Its **two buttons** become volume
down/up; its **10 NeoPixels** become a **real-time amplitude / VU meter of the
music as it plays** (not a volume gauge). One micro-USB cable carries both power
and bidirectional serial. This realizes the "bidirectional serial satellite" hook
already designed into [`PLAN.md`](PLAN.md) §Key-decisions ("a general bidirectional
serial channel … volume input *and* a NeoPixel-ring level/VU meter driven by a
lightweight Pi-computed audio-level envelope (NOT FFT)").

This is the richer of the two volume routes. The minimal alternative — two plain
pushbuttons straight to GPIO, no CPX — is documented separately in
[`GPIO-VOLUME-BUTTONS-PLAN.md`](GPIO-VOLUME-BUTTONS-PLAN.md).

---

## Context

The player has **no volume control today**. [`PLAN.md`](PLAN.md) §Key-decisions
reserved an "ALSA mixer get/set, wired but bound to no gesture" capability, but it
was never built — `controller.py` only does play/pause, next, and shutdown. The one
arcade button is full (single = play/pause, double = next, long = shutdown), so
volume needs a **new input source**, and the family also wants the lights to *do
something* with the music. The CPX gives us both in one USB-attached unit without
touching the bonnet's occupied header.

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
- **NeoPixels show the *music's amplitude*, not the volume level.** The Pi computes
  a lightweight level envelope from the playing audio and streams it; the CPX
  renders the VU animation locally.

---

## Architecture

A **self-contained satellite service**, independent of the root controller. It does
three things and talks to Mopidy directly for volume — so it needs no IPC into the
controller and can run **unprivileged** (`brew`, groups `dialout` + `audio`). This
mirrors the existing 4-service split
(`walkman-{mopidy,controller,jack,autoplay}.service`); the satellite is a 5th unit
modeled on [`walkman-jack.service`](../systemd/walkman-jack.service).

```
 CPX (CircuitPython)                         Pi (walkman-satellite.service)
 ───────────────────                         ──────────────────────────────
 BUTTON_A ─┐                          ┌───── serial read loop ── nudge_volume(-step)
 BUTTON_B ─┤  USB CDC (data)  <──────>┤                          via MopidyClient
 NeoPixels ┘   single cable           └───── serial write loop ◄─ audio meter loop
   (VU ring, local animation)            L:<0-255>            (ALSA tap → audioop.rms)
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
max = 100     # lower this for a kid-safe ceiling
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
| `config/walkman.toml` | `[volume]` + `[satellite]` sections |
| `src/walkman/mopidy_client.py` | volume helpers (shared groundwork above) |

New dependency: **pyserial** (everything else is stdlib). Add to `setup.sh` /
README deps when built.

### `src/walkman/satellite.py` — one process, three loops

- **Serial read loop.** Find the CPX via `/dev/walkman-cpx` (udev symlink, below) or
  fall back to scanning `/dev/serial/by-id/*` for the Adafruit VID `239A`. Open the
  **data** CDC endpoint (see boot.py note), read line-framed events: `A` →
  `nudge_volume(-step)`, `B` → `nudge_volume(+step)`. Wrap each action so a
  `MopidyError` can never kill the read loop (mirror `button.py`'s "never let an
  action error kill the input thread").
- **Audio meter loop.** Read PCM frames from the ALSA tap (see "The hard part"),
  compute RMS with stdlib **`audioop.rms`** (C-fast; present in Python 3.11 — note
  it's deprecated in 3.13, so pin a fallback), apply smoothing + short peak-hold,
  map to a single **0–255** scalar.
- **Serial write loop.** Emit `L:<0-255>\n` at ~20–30 Hz. The CPX maps the scalar
  onto its 10-pixel ring and runs the decay/peak animation **locally**, so dropped
  or jittery frames don't stutter the meter and the serial stays light.

### `cpx/boot.py` + `cpx/code.py` — CircuitPython firmware

- **`boot.py`:** `usb_cdc.enable(console=True, data=True)` so the data channel is
  separate from the REPL console. The Pi then opens the **second** ACM device — the
  `…-if02` data interface — not the console; the udev rule pins this.
- **`code.py`:**
  - Poll `board.BUTTON_A` / `board.BUTTON_B` with edge detection + debounce; emit
    exactly one `A` / `B` per press (one-step-per-press).
  - Drive `board.NEOPIXEL` (10 px) from incoming `L:` frames: a VU bar with a
    green → amber → red gradient and a brief peak-hold pixel; calm global brightness
    (kids). On `S:pause` (optional) idle the ring (off or a slow dim pulse).
  - Keep the parser tolerant of partial lines (accumulate until `\n`).

### `systemd/walkman-satellite.service`

Model on [`walkman-jack.service`](../systemd/walkman-jack.service):
`After=walkman-mopidy.service`, `Wants=walkman-mopidy.service`, `Restart=always`,
`RestartSec=3`, `User=brew`, `SupplementaryGroups=dialout audio`.

### `config/99-walkman-cpx.rules`

udev rule matching the CPX VID:PID → `SYMLINK+="walkman-cpx"` (and, if needed,
`MODE="0660" GROUP="dialout"`), so the device path is deterministic across replug
and boots. Resolve the exact PID for the **data** interface on the bench.

### `config/walkman.toml` — `[satellite]`

```toml
[satellite]
device = "/dev/walkman-cpx"   # udev symlink; falls back to /dev/serial/by-id scan
volume_step = 5               # falls back to [volume].step if unset
level_hz = 25                 # NeoPixel level updates per second
smoothing = 0.4               # 0..1 envelope smoothing
peak_hold_ms = 350
```

---

## The hard part — tapping the playing audio for the envelope ⚠️

**This is the #1 risk. Prototype it on the Pi before building the rest.** Mopidy
must keep playing through the bonnet exactly as today while the satellite *also*
gets a copy of the samples to measure.

### Recommended: ALSA tee → loopback → meter

Fan Mopidy's output to **both** the real bonnet card and a capturable loopback,
without changing what the user hears:

1. Load `snd-aloop` (creates a `Loopback` card: what's played to `hw:Loopback,0`
   appears as capture on `hw:Loopback,1`).
2. Define an ALSA `pcm` that tees Mopidy's output to two slaves — the real card
   `plughw:CARD=aiyvoicebonnet` **and** the loopback playback — via `type multi` /
   `type route`, or a GStreamer `output = ... ! tee` in `mopidy.conf`.
3. The meter reads the loopback **capture** side: **one writer, one reader** → no
   `dsnoop` needed. Compute `audioop.rms` on coarse chunks.

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
| Pi → CPX *(opt)* | `S:play` / `S:pause` | ring idle/active behavior |

Line-framed ASCII keeps both ends trivial and self-resynchronizing if a byte drops.

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

- **ALSA tee glitch-freeness is unproven on this codec/CPU** — the dominant risk;
  prototype first; a dead meter must not stall playback.
- **Which ACM is the data channel.** With `console=True, data=True` the CPX exposes
  two CDC endpoints; the Pi must open the **data** one. Pin it with the udev rule by
  interface, and verify the `…-if02` mapping on the bench.
- **`audioop` deprecation.** Stdlib in 3.11 (what the Pi runs) but removed in 3.13 —
  note a fallback (`numpy`, or a hand-rolled RMS) for a future OS bump.
- **USB port is singular.** The Zero 2 W has one USB data port; the CPX consumes it
  (see [`PLAN.md`](PLAN.md) §Satellite hook). No other USB peripheral can share it
  without a hub.
- **CircuitPython on a 2018 CPX** — confirm the installed CircuitPython version
  supports `usb_cdc`; update the UF2 if needed. Document the pinned version.
- **Latency.** The tee adds a buffer or two of delay between sound and lights;
  acceptable for a VU meter, but tune buffer sizes if it feels laggy.

---

## Verification (end-to-end, on the real Pi)

1. **Shared volume path:** from the Pi, `core.mixer.get_volume` /
   `set_volume` move audible volume on **both** speaker and headphone, and the level
   survives a `systemctl restart walkman-mopidy` (`restore_state`).
2. **Enumeration:** plug the CPX → `/dev/walkman-cpx` appears; the satellite service
   comes up and logs that it found the data channel.
3. **Buttons:** press A / B → volume steps one increment each (confirm by ear and via
   `get_volume`); holding a button does nothing extra (one-step-per-press).
4. **VU meter:** start playback → the NeoPixel ring tracks the music's amplitude in
   real time; pause → the ring idles.
5. **Robustness:** `systemctl stop walkman-satellite` mid-song → **audio keeps
   playing without a glitch** (the `snd-aloop` drain gotcha); `start` again → meter
   resumes.

---

## Out of scope

- No volume *gesture* on the arcade button (it stays full: single/double/long).
- Repeat-track and now-playing OLED stay in [`IDEAS.md`](IDEAS.md) — though both are
  natural future passengers on this same serial link once it exists.
- The standalone GPIO-button route lives in
  [`GPIO-VOLUME-BUTTONS-PLAN.md`](GPIO-VOLUME-BUTTONS-PLAN.md).
