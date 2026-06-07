# Walkman — One-Button Headless Music Player (AIY Voice Bonnet)

## Context

Building a screen-free, one-button YouTube Music player for kids on a Raspberry Pi
Zero 2 W with a Google AIY Voice Bonnet. Boots straight into shuffle-playing one
configured playlist through the bonnet's headphone jack; a single arcade button +
its built-in RGB LED are the only I/O. The build runs on the Mac, driving the Pi
through a tmux/SSH session (`brew@walkman-a.local`); all hardware commands execute
on the Pi.

This plan is grounded in **live read-only probing of the actual Pi** (not
assumptions). Two findings change the brief's stated approach:

1. **No LED kernel driver exists.** `leds-ktd202x` is absent from kernel
   `6.12.87+rpt-rpi-v8` — not a loadable module, not built-in, not in the kernel
   config; `/lib/modules/.../drivers/leds/` ships only pca/is31 drivers. The
   brief's premise (a regulator/supplier dependency is *blocking* the driver from
   binding) is not the real cause: `waiting_for_supplier` reads `0`, dmesg shows no
   deferred probe — there is simply **no driver to bind**. → Production LED control
   will use **raw I2C via `smbus2`** (user-approved), with DKMS noted as a future
   option.
2. **Audio path is well-supported.** Both `googlevoicehat-soundcard.dtbo` and the
   `snd-soc-googlevoicehat-codec` module are present on disk; only the overlay line
   + reboot are missing. Codec confirmed on I2C at `0x1a`.

Verified state: Pi Zero 2 W, Bookworm 64-bit, kernel 6.12.87; I2C devices at
`0x1a/0x31/0x33/0x52`; HAT EEPROM identifies as "AIY VoiceBonnet"; `aplay -l` shows
only `vc4hdmi`; `/sys/class/leds` has no bonnet entry; **mopidy not installed**;
`smbus2`/`lgpio`/`gpiozero`/`nmcli`/`amixer`/`i2cset` present; NetworkManager is the
net stack; **~270 MB RAM free** (tight → favors one combined controller process).

## Key decisions (with justification)

- **LED control = raw I2C (`smbus2`) to KTD2026 @ `0x31`.** No kernel driver exists.
  A small `KTD2026` Python class handles reset, channel/current registers, and the
  documented post-reset "Write failed" glitch via short retries. DKMS route
  documented in README as future work.
- **Mopidy control = HTTP JSON-RPC (port 6680), built into Mopidy core.** No extra
  extension needed (MPD would require Mopidy-MPD + a persistent socket). A
  short-timeout `POST /mopidy/rpc` doubles as the reachability probe that drives the
  blue/red LED states, and exposes the full core API (playback, tracklist, mixer).
- **One combined controller process** (button + LED + Mopidy control), not three.
  Justification: ~270 MB RAM; the LED reflects player state the controller already
  polls, so folding them avoids IPC and a third process. → **Two services total:**
  `mopidy.service` (stock) + `walkman.service` (our controller).
- **Pluggable input layer.** Input is abstracted behind a small dispatch interface
  (gesture/command -> action), so the on-board button is just the first source. A
  future satellite source (see below) and a future volume input plug in without a
  rewrite. No extra GPIO pins reserved now.
- **Volume = real capability now, no input yet.** Wire ALSA mixer get/set
  (`amixer`/python) into the controller and verify it works programmatically, but
  bind it to no gesture (the single button stays play/pause + next + shutdown).
  Future volume sources documented, not built.
- **Auto-play on boot lives in the controller**: once Mopidy HTTP is reachable, it
  loads the configured playlist, sets `random=true repeat=true consume=false`, and
  starts playback — satisfying "music with zero interaction."
- **YTMusic auth = OAuth refresh-token flow, but proven before committing.** OAuth
  is durable (avoids silent cookie expiry), but Google has been tightening "TV and
  Limited Input devices" clients for YouTube scopes — it's the likeliest thing to
  fail. ytmusicapi 1.x requires a Google Cloud OAuth client of that type + YouTube
  Data API v3 enabled; `client_id`/`client_secret` feed a device-code flow producing
  `oauth.json` (refresh token). **In Step 1's test-with-my-account session we prove
  it actually streams end-to-end; if it can't be made to work in a reasonable
  attempt, fall back to cookie/header auth then** (documented periodic re-auth) —
  rather than building the whole design around OAuth and discovering the gap late.
- **Per-device credentials.** The OAuth json path and playlist ID live in the
  user-settings config, never in code. Provisioning unit #2 = drop a different
  `oauth.json`, change playlist ID, change hostname — nothing else.
- **Satellite expansion hook = a general bidirectional serial channel (not built
  now).** Future external I/O is conceived as a two-way command/data link to a
  satellite microcontroller (likely an Adafruit Circuit Playground Express over USB
  serial — USB-powered + USB-data on one cable). It will likely later carry volume
  input *and* a secondary status display *and* a NeoPixel-ring level/VU meter driven
  by a lightweight Pi-computed audio-level envelope (NOT FFT) plus volume/play
  state. The pluggable input layer is therefore designed as a general bidirectional
  message channel so any of those can attach later without rework. README will note:
  the Pi Zero 2 W's single USB data port is the real constraint for this path, and an
  analog LM3915 VU ladder is a separate optional hardware project, not the meter.

## Repository layout (authored on Mac at `/Users/brew/Code/walkman`, deployed to Pi)

```
walkman/
  README.md
  setup.sh                      # idempotent installer
  config/
    walkman.toml                # USER settings: playlist, oauth path, hostname,
                                #   long-press threshold, colors, pulse speed
    mopidy.conf.example         # references oauth path + ALSA device
  src/walkman/
    ktd2026.py                  # raw-I2C KTD2026 driver (+ retry, color map)
    led.py                      # state->color map + sine breathing effect
    mopidy_client.py            # HTTP JSON-RPC client + reachability/backoff
    inputs.py                   # pluggable input dispatch (gesture/cmd -> action);
                                #   abstraction for button now + serial satellite later
    button.py                   # gpiozero source: single/double/long state machine
    controller.py               # main loop: inputs->actions, state->LED, autoplay,
                                #   volume (ALSA mixer) capability
  systemd/
    walkman.service
  scripts/
    ytmusic_oauth.py            # guided OAuth device-code walkthrough helper
    led_probe.py                # step-0: light each channel, pin color mapping
  docs/
    config.txt.overlay          # the exact line(s) added, documented
```

## Execution plan (in brief's order; **step 0 is a hard gate**)

### Step 0 — Prove the two foundations, then STOP and report

**0a. Audio bring-up**
1. `sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.bak-walkman` (backup).
2. Append under `[all]`: `dtoverlay=googlevoicehat-soundcard` (the present `.dtbo`).
3. **Reboot** (`sudo reboot`) → SSH drops in tmux; wait ~45s; reconnect
   `ssh brew@walkman-a.local`; confirm with `cat /proc/device-tree/model`.
4. Verify a second card appears in `aplay -l`; unmute/raise with `amixer -c <n>`;
   play an audible test tone: `speaker-test -c2 -twav -l1 -D plughw:<card>` (and a
   `aplay` WAV). **Listen for it from the bonnet jack.**
5. Record the working ALSA device string for `mopidy.conf` and `walkman.toml`.

**0b. LED bring-up (raw I2C)**
1. Run `scripts/led_probe.py`: software reset (`0x00<-0x07`), then drive each of
   the three channels individually via enable reg `0x04` + current regs
   `0x06/0x07/0x08`, with short retries around the post-reset glitch.
2. **Visually confirm** color changes and **pin down channel→R/G/B mapping**
   (unknown today). Bake the resolved map into `ktd2026.py`.
3. Confirm clean off/on and a brightness sweep.

→ **Report results before any app logic** (brief rule #1). If audio is inaudible or
the LED can't be driven cleanly, stop.

### Step 1 — Mopidy + YTMusic, manual playback
- `setup.sh` installs: `mopidy` (apt), then **in an isolated pip line**
  `Mopidy-YTMusic` + `ytmusicapi` with `--break-system-packages`; record the exact
  resolved versions into README (fragile, unofficial API).
- Author `mopidy.conf` (ALSA output = bonnet device from 0a; httpd enabled;
  `[ytmusic]` auth json path from config).
- **OAuth walkthrough (done together later):** create Google Cloud OAuth "TV and
  Limited Input devices" client + enable YouTube Data API v3 →
  `scripts/ytmusic_oauth.py` runs the device-code flow → writes `oauth.json` to the
  documented per-device path. For *today's* test, use your own account/playlist as
  the harness.
- Manually: start mopidy, load the playlist, enable shuffle+repeat, confirm audio.
- **Prove OAuth streams end-to-end here** (my test account/playlist). If the device-
  code OAuth flow can't be made to actually stream in a reasonable attempt, fall
  back to cookie/header auth at this point and document periodic re-auth — don't
  carry an unproven OAuth assumption into later steps.

### Step 2 — Auto-start on boot
- Enable stock `mopidy.service`. Install `walkman.service` (After/Wants mopidy +
  network-online). Controller, on start, waits for Mopidy HTTP (backoff, blue
  blink), loads playlist, sets random/repeat/consume, **starts playback**.

### Step 3 — Input layer + button gestures (`button.py`, GPIO 23)
- **Pluggable input layer first:** a small dispatch interface mapping abstract
  gestures/commands -> controller actions (play/pause, next, shutdown, set-volume).
  The on-board button is the first source registered against it; a future serial
  satellite source and a future volume source plug into the same interface.
- gpiozero `Button(23, pull_up=True)` (lgpio backend). State machine on
  press/release timestamps: **long** = held ≥ threshold (default 1.2 s, from
  config); after release, a short window (~350 ms) disambiguates **single** vs
  **double**. Map: **single → play/pause toggle; double → next track; long → safe
  shutdown** (show distinct "shutting down" LED, then `sudo poweroff`). Reshuffle is
  dropped (too close to "next"); safe shutdown mitigates SD-card corruption from a
  kid yanking power.
- **Volume capability now (no input yet):** wire ALSA mixer get/set into the
  controller (`amixer`/python), expose a `set_volume`/`get_volume` action, and
  verify programmatically. Bind it to no gesture. No GPIO pins reserved. Future
  volume sources documented in README.

### Step 4 — LED status + breathing (`led.py`)
- State→color: **playing** green sine-breathing; **paused** amber steady;
  **startup/wifi-down/mopidy-unreachable** blue blink; **error** (mopidy down after
  retries) red blink; **auth expired / needs re-login** distinct slow **magenta
  blink** (cookie/header auth expires periodically — surface it on the LED so the
  user knows to re-auth); **shutting down** a distinct cue (e.g. magenta/white steady or
  slow fade-to-off) held until poweroff so it's safe to cut power. Breathing =
  lightweight sine over brightness at ~20–30 fps *only while playing* (no FFT);
  steady/blink states need no tight loop. Player state polled at ~1 Hz via JSON-RPC;
  wifi via `nmcli`/connectivity check.

### Step 5 — README + hardening
- Mopidy reachability retry/backoff; wifi-drop reflected on LED and auto-recovers.
- `setup.sh` idempotent and re-runnable. README covers: exact OS, setup, the OAuth
  walkthrough (+ cookie-auth fallback), per-device credential swap procedure, how to
  set the playlist, enabling services, **Known uncertainties**, and the future
  input/satellite roadmap.
- **Future input/satellite docs (README, not built):** the bidirectional serial
  channel to a CPX (volume + secondary status display + NeoPixel VU meter from a
  lightweight Pi-side level envelope, not FFT); the **single USB data port** is the
  real constraint; an LM3915 analog VU ladder is a separate optional hardware
  project, not the meter; pluggable volume-input options.
- **Power-loss resilience (README, actionable, deferred implementation):** concrete
  read-only-root / overlayfs procedure — `raspi-config` → Performance → Overlay File
  System (and the manual `cmdline.txt`/`fstab` equivalent), what breaks while
  read-only (config edits, oauth re-auth) and how to temporarily disable it to make
  changes. A real follow-the-steps procedure, since kids will yank power.

## Critical files to reuse / not reinvent
- Existing on-Pi assets: `/boot/firmware/overlays/googlevoicehat-soundcard.dtbo`
  and module `snd-soc-googlevoicehat-codec` (audio — do not hand-roll an overlay).
- Python deps already present: `smbus2`, `gpiozero`, `lgpio` (no new system deps for
  LED/button). `requests` for JSON-RPC (confirm/add in setup.sh).
- Mopidy built-in `mopidy.http` JSON-RPC (no Mopidy-MPD needed).

## Verification (end-to-end, on the real Pi)
- **Step 0 gate:** test tone audible from bonnet jack; each LED color visibly
  driven and channel map confirmed.
- **Playback:** after reboot, music auto-plays with zero interaction.
- **Gestures:** single = pause/resume; double = track changes; long = "shutting
  down" LED then clean poweroff. Volume verified programmatically (no gesture).
- **LED states:** green breathing while playing; amber when paused; blue blink
  during startup/`systemctl stop mopidy`; red blink after retries exhausted;
  distinct shutting-down cue on long-press.
- **Resilience:** `nmcli` wifi down → blue; restore → recovers to green/amber.
  `systemctl restart walkman` and a sudden power-cycle both return to playing.
- Build toward passing the user's independent `verify.sh`.

## Known uncertainties (to confirm during build)
- **KTD2026 channel→color mapping** unknown until step 0b probe.
- **`leds-ktd202x` driver absent** — raw I2C is the chosen path; DKMS/kernel rebuild
  is the only route to `/sys/class/leds` and is deferred.
- **Mopidy-YTMusic/ytmusicapi fragility** — version pinned + isolated; OAuth needs a
  user-created Google Cloud TV client (done together). If OAuth proves unworkable,
  fall back to cookie/header auth with documented periodic re-auth.
- **RAM headroom (~270 MB free)** — watch Mopidy footprint while streaming; combined
  controller chosen partly for this.
