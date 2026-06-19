# Walkman — one-button headless YouTube Music player

A screen-free music player for kids on a **Raspberry Pi Zero 2 W** + **Google AIY
Voice Bonnet**. Power it on and it boots straight into shuffle-playing one YouTube
Music playlist. The built-in control surface is a single arcade button + RGB LED;
the CPX satellite adds volume buttons and a secondary light display.

> **Status (2026-06):** Core playback, install/provisioning, CPX volume/Night mode,
> and re-auth tooling are in place. See
> [`docs/WORKLOG.md`](docs/WORKLOG.md) for the full build story (and every yak-shave),
> and [`docs/IDEAS.md`](docs/IDEAS.md) for the deferred-robustness backlog.

## What it does
- **Boot → music**: auto-starts Mopidy and shuffle-plays the configured playlist, zero interaction.
- **One button** (GPIO 23): **single** = play/pause · **double** = next track · **long-press (~1.2 s)** = safe shutdown (white LED, then clean power-off).
- **RGB LED status**: green breathing = playing · amber = paused · blue blink = booting / wifi or Mopidy not ready · red blink = Mopidy down · **magenta blink = auth file missing / known re-auth needed** · white = shutting down / safe to unplug. Expired-cookie auto-detection is still a roadmap item.
- **Headphones auto-switch**: plug headphones into the 3.5 mm jack → the built-in speaker mutes; unplug → it returns. (Detected on insert/remove; see Known limitations.)
- **CPX satellite**: Circuit Playground Express buttons adjust volume; its NeoPixels show a green circular VU meter. The slide switch enables **Night mode**: no VU meter, with only a very dim blue volume cue after volume presses.

## Hardware / OS
- Raspberry Pi Zero 2 W; **Raspberry Pi OS Lite 64-bit (Debian Bookworm)**, kernel `6.12.87+rpt-rpi-v8`.
- AIY Voice Bonnet: **RT5645** codec (I²C 0x1a), **AIY IO MCU** (0x52), **KTD2026** button LED (0x31). The stock kernel ships none of these drivers — we build them (see below).
- Adafruit Circuit Playground Express on the Pi's USB data port, running CircuitPython with USB CDC data enabled.
- Built/operated from a Mac over SSH to `brew@walkman-a.local` (passwordless key). All hardware commands run on the Pi.

## Install (fresh unit)
1. Flash **Raspberry Pi OS Lite 64-bit (Bookworm)** with `rpi-imager`; in its settings set the **hostname**, user **`brew`**, **wifi**, and your **SSH key**.
2. Get the repo onto the Pi at **`/home/brew/walkman`** (the systemd units hardcode that path):
   ```bash
   git clone https://github.com/brianbrewington/aiy-walkman.git /home/brew/walkman   # private repo; or scp/rsync it over
   ```
3. Run the idempotent installer (safe to re-run):
   ```bash
   cd /home/brew/walkman && sudo ./setup.sh
   sudo reboot
   ```
   `setup.sh` installs the AIY DKMS drivers (from `drivers/prebuilt/`), disables the
   SoC's built-in audio, installs `deno` + Mopidy + Mopidy-YouTube + yt-dlp (and
   **removes `brotli`**, which segfaults in Mopidy on ARM), the player-client shim, the
   five `walkman-*` services, the CPX udev rule, the ALSA loopback VU tap, and the
   calm ALSA mixer baseline.
4. Set **this unit's account** (cookies + playlist) — see next section.
5. Flash + provision the CPX satellite — **follow [`docs/CPX-BOARD-BRINGUP.md`](docs/CPX-BOARD-BRINGUP.md)**.
   A fresh CPX usually needs a CircuitPython upgrade first (the firmware needs ≥ 7.0
   for the second USB data channel), and `cpx/code.py` must be shipped **precompiled**
   to a `.mpy` + tiny launcher because the board's 32 KB RAM can't compile it
   on-device. The runbook has the exact, copy-pasteable steps and the gotchas.
6. Power-cycle → it boots into that kid's music.

> **Each unit needs a unique hostname** (`walkman-a`, `walkman-b`, …) — two boxes with
> the same name collide on the network (both answer to `walkman-a.local`). Set it in
> rpi-imager (step 1) or with `walkman-account.sh --hostname walkman-b` (step 4).
>
> **Golden-image option:** after steps 1–4 on one unit, `dd` its SD card as a template;
> a new unit is then *flash the image* → **rename the host first** (it inherits the
> template's name!) → *step 4* (swap the account). Rename via
> `sudo hostnamectl set-hostname walkman-b` (or `walkman-account.sh --hostname`), then
> reboot. `setup.sh` remains the versioned source of truth; the image is a convenience
> snapshot.

## YouTube Music auth (per device) + re-auth ("cookie-monster")
Auth is **per device** (each unit can be a different kid's account). It uses a
browser **cookie** file (durable OAuth isn't supported by the maintained extension —
see `docs/WORKLOG.md`). Cookies expire periodically; when playback stops working
because auth has gone stale, re-run the same cookie-monster step. The magenta LED
currently means the auth file is missing or the box already knows setup is needed;
automatic expired-cookie detection is still deferred.

**Get a cookies.txt:** on a computer logged into the target YouTube Music account,
export `music.youtube.com` cookies with the open-source **"Get cookies.txt LOCALLY"**
browser extension. Chrome disables extensions in Incognito by default, so first enable
the extension there: `chrome://extensions` → **Get cookies.txt LOCALLY** → **Details** →
turn on **Allow in Incognito** (no need to pack/side-load — this one toggle is all it
takes). In Incognito the icon lives in the puzzle-piece (🧩) menu even if pinned.

> **Export from an Incognito window, then close it — this is the difference between a
> cookie that lasts weeks and one that dies in an hour.** Google rotates the
> `__Secure-*PSIDTS` session cookies as you browse; if you export from your normal
> window and keep using YouTube, the snapshot on the device is invalidated server-side
> almost immediately (we hit exactly this in field test #1 — a complete, valid export
> that authenticated fine, then went dead within the hour). An Incognito session you
> close right after exporting isn't rotated out from under the box. Prefer a **personal**
> Google account; Workspace/school accounts may enforce short, device-bound sessions.

**Provision an account (or refresh it):**
```bash
# on the Pi (cookies.txt already copied over):
~/walkman/scripts/walkman-account.sh --cookies cookies.txt --playlist <PLAYLIST_ID> [--hostname walkman-b]
```
- Provisioning a new unit: pass `--cookies`, `--playlist`, and `--hostname`.
- **Re-auth (cookie-monster):** just `--cookies` — same script, playlist unchanged.

**One-liner re-auth from your laptop** (no need to SSH in first):
```bash
scp cookies.txt brew@walkman-a.local:/tmp/c.txt && \
ssh -t brew@walkman-a.local '~/walkman/scripts/walkman-account.sh --cookies /tmp/c.txt && rm -f /tmp/c.txt'
```
`walkman-account.sh` also removes cookies handed to it under `/tmp` on any exit, so
a failed playlist start should not leave the copied cookie sitting on the Pi.

The playlist ID is the `list=` value from a playlist URL
(`https://music.youtube.com/playlist?list=PL...`). The auth file lives at
`~/.config/walkman/ytmusic-auth.json` (mode 600) and is referenced from
`config/walkman.toml` / `mopidy.conf` — never hardcoded.

## Services
| service | role |
|---|---|
| `walkman-mopidy` | Mopidy (YouTube Music) audio server; loads the player-client shim via `PYTHONPATH` |
| `walkman-satellite` | CPX USB serial + volume buttons + NeoPixel VU meter |
| `walkman-autoplay` | oneshot: waits for Mopidy + network, loads the playlist, shuffle-plays |
| `walkman-controller` | button gestures → actions, and the player-state → LED status |
| `walkman-jack` | mutes the speaker when headphones are plugged in |

Config: `config/walkman.toml` (playlist, button thresholds, volume cap, CPX satellite,
LED brightness/breathe);
`~/.config/mopidy/mopidy.conf` (audio device, YouTube Music auth path).

Logs go to journald. `setup.sh` installs `/etc/systemd/journald.conf.d/walkman.conf`
to cap persistent logs at 64 MB, keep 128 MB free, split files at 8 MB, and retain at
most 14 days. That keeps enough history for debugging without letting a bad YouTube
or CPX loop fill the SD card.

## CPX controls
- **Button A**: volume down by 5, capped by `[volume].min` / `[volume].max`.
- **Button B**: volume up by 5, capped by `[volume].min` / `[volume].max`.
- **Normal slide-switch position**: green circular VU meter from the actual Pi playback stream.
- **Night mode slide-switch position**: VU off; volume presses show only a very dim blue level bar for 2 seconds.
- **Diagnostics**: the Pi can send `Q` over the CPX data channel; the CPX replies
  with `S:<night>,<mode>,<volume>,<level>` so logs can confirm its logical light state.
  `walkman-satellite` logs low-rate control frames in journald (`A`, `B`, `C:`, `Q`,
  `V:`, `S:`) but intentionally does not log the 25 Hz `L:` level stream. The CPX
  firmware does not write logs to its tiny flash filesystem. The `C:` frame only
  sends render settings; the physical slide switch still decides whether Night mode
  is active.

## Known limitations / uncertainties
- **YouTube anti-bot fragility:** playback relies on `yt-dlp` + the unofficial
  `ytmusicapi`, which YouTube actively fights (signature solving + PO tokens). Versions
  are pinned; expect periodic breakage. Manual `next` occasionally lands on a track
  YouTube won't resolve (press again). Robust+fast fix if needed:
  `bgutil-ytdlp-pot-provider`. Full detail in `docs/WORKLOG.md`.
- **Jack detect at boot:** if the device boots with a plug already in the jack, the
  RT5645 detect reads "empty" until you replug. Logged in `docs/IDEAS.md`.
- **CPX data interface mapping:** the udev rule targets CircuitPython's USB CDC data
  interface. If `/dev/walkman-cpx` does not appear, confirm `cpx/boot.py` is installed
  and check which `/dev/ttyACM*` endpoint has interface number `02`. A healthy setup
  has `/dev/walkman-cpx` present and `systemctl status walkman-satellite` running.
- **VU tap dependency:** the green VU meter depends on `snd-aloop` plus the Mopidy
  GStreamer tee in `mopidy.conf`; stopping `walkman-satellite` may affect the loopback
  drain until systemd restarts it.
- **Kernel drift:** the AIY drivers are patched for kernel 6.12; a newer kernel may
  break them. Hold/pin the kernel or rebuild from source (`drivers/patches/`).

## Deferred / roadmap
- **Robustness (next pass):** jack-detect-at-boot fix, wifi auto-recovery, overlayfs/
  read-only-root for power-loss resilience, expired-cookie auto-trigger. See `docs/IDEAS.md`.
- **Future satellite passengers:** repeat-track and a now-playing LCD are still good
  fits for the CPX/serial path. See `docs/IDEAS.md`.

## Tests
Pure-logic unit tests (no Pi/hardware needed — run on any machine):
```bash
python3 -m unittest discover -s tests        # or: pytest tests/
```
They cover the gesture state machine, LED rendering + shutdown latch, the Mopidy
JSON-RPC client, CPX satellite helpers, CPX firmware protocol/rendering with fake
serial/buttons/NeoPixels, autoplay, and the YouTube auth converter — including
regression guards for the bugs we hit (next-on-paused→silence, single-vs-double,
SAPISIDHASH).

## Docs map
- `docs/SETUP_PLAYLIST_AND_COOKIES.html` — the **hand-to-a-kid** interactive setup guide
  (playlist + cookies; open in a browser)
- `docs/WORKLOG.md` — full chronological build narrative + gotchas (read this if stuck)
- `docs/POWER-LOSS.md` — safe-shutdown + overlayfs resilience
- `docs/CPX-BOARD-BRINGUP.md` — **flash + bring up the CPX on a new unit** (CircuitPython upgrade, the 32 KB-RAM `.mpy` gotcha, verify steps, troubleshooting)
- `docs/CPX-VOLUME-PLAN.md` — CPX satellite design + protocol reference
- `docs/ROBUSTNESS-NOTES.md` — tests + the robustness pass
- `docs/STEP0-NOTES.md … STEP5-NOTES.md` — historical checkpoints; useful for learning
  what happened, not the current install path
- `docs/IDEAS.md` — backlog / deferred robustness / future features
- `docs/PLAN.md` — historical build plan and original tradeoffs
- `drivers/` — prebuilt AIY DKMS debs + the source patches
