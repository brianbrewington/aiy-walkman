# Walkman — one-button headless YouTube Music player

A screen-free music player for kids on a **Raspberry Pi Zero 2 W** + **Google AIY
Voice Bonnet**. Power it on and it boots straight into shuffle-playing one YouTube
Music playlist. A single arcade button + its built-in RGB LED are the only I/O.

> **Status (2026-06):** Steps 0–4 complete and verified on hardware; Step 5
> (reproducible install + provisioning + re-auth) in place. See
> [`docs/WORKLOG.md`](docs/WORKLOG.md) for the full build story (and every yak-shave),
> and [`docs/IDEAS.md`](docs/IDEAS.md) for the deferred-robustness backlog.

## What it does
- **Boot → music**: auto-starts Mopidy and shuffle-plays the configured playlist, zero interaction.
- **One button** (GPIO 23): **single** = play/pause · **double** = next track · **long-press (~1.2 s)** = safe shutdown (white LED, then clean power-off).
- **RGB LED status**: green breathing = playing · amber = paused · blue blink = booting / wifi or Mopidy not ready · red blink = Mopidy down · **magenta blink = YouTube auth expired, run the cookie-monster** · white = shutting down / safe to unplug.
- **Headphones auto-switch**: plug headphones into the 3.5 mm jack → the built-in speaker mutes; unplug → it returns. (Detected on insert/remove; see Known limitations.)

## Hardware / OS
- Raspberry Pi Zero 2 W; **Raspberry Pi OS Lite 64-bit (Debian Bookworm)**, kernel `6.12.87+rpt-rpi-v8`.
- AIY Voice Bonnet: **RT5645** codec (I²C 0x1a), **AIY IO MCU** (0x52), **KTD2026** button LED (0x31). The stock kernel ships none of these drivers — we build them (see below).
- Built/operated from a Mac over SSH to `brew@walkman-a.local` (passwordless key). All hardware commands run on the Pi.

## Install (fresh unit)
1. Flash **Raspberry Pi OS Lite 64-bit (Bookworm)** with `rpi-imager`; in its settings set the **hostname**, user **`brew`**, **wifi**, and your **SSH key**.
2. Get the repo onto the Pi at **`/home/brew/walkman`** (the systemd units hardcode that path):
   ```bash
   git clone <this-repo> /home/brew/walkman      # or scp it over
   ```
3. Run the idempotent installer (safe to re-run):
   ```bash
   cd /home/brew/walkman && sudo ./setup.sh
   sudo reboot
   ```
   `setup.sh` installs the AIY DKMS drivers (from `drivers/prebuilt/`), disables the
   SoC's built-in audio, installs `deno` + Mopidy + Mopidy-YouTube + yt-dlp (and
   **removes `brotli`**, which segfaults in Mopidy on ARM), the player-client shim, the
   four `walkman-*` services, and the calm ALSA mixer baseline.
4. Set **this unit's account** (cookies + playlist) — see next section.
5. Power-cycle → it boots into that kid's music.

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
see `docs/WORKLOG.md`). Cookies expire periodically; when they do, the LED blinks
**magenta** and you re-run the same step.

**Get a cookies.txt:** on a computer logged into the target YouTube Music account,
export `music.youtube.com` cookies with the open-source **"Get cookies.txt LOCALLY"**
browser extension.

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
The playlist ID is the `list=` value from a playlist URL
(`https://music.youtube.com/playlist?list=PL...`). The auth file lives at
`~/.config/walkman/ytmusic-auth.json` (mode 600) and is referenced from
`config/walkman.toml` / `mopidy.conf` — never hardcoded.

## Services
| service | role |
|---|---|
| `walkman-mopidy` | Mopidy (YouTube Music) audio server; loads the player-client shim via `PYTHONPATH` |
| `walkman-autoplay` | oneshot: waits for Mopidy + network, loads the playlist, shuffle-plays |
| `walkman-controller` | button gestures → actions, and the player-state → LED status |
| `walkman-jack` | mutes the speaker when headphones are plugged in |

Config: `config/walkman.toml` (playlist, button thresholds, LED brightness/breathe);
`~/.config/mopidy/mopidy.conf` (audio device, YouTube Music auth path).

## Known limitations / uncertainties
- **YouTube anti-bot fragility:** playback relies on `yt-dlp` + the unofficial
  `ytmusicapi`, which YouTube actively fights (signature solving + PO tokens). Versions
  are pinned; expect periodic breakage. Manual `next` occasionally lands on a track
  YouTube won't resolve (press again). Robust+fast fix if needed:
  `bgutil-ytdlp-pot-provider`. Full detail in `docs/WORKLOG.md`.
- **Jack detect at boot:** if the device boots with a plug already in the jack, the
  RT5645 detect reads "empty" until you replug. Logged in `docs/IDEAS.md`.
- **Kernel drift:** the AIY drivers are patched for kernel 6.12; a newer kernel may
  break them. Hold/pin the kernel or rebuild from source (`drivers/patches/`).

## Deferred / roadmap
- **Robustness (next pass):** jack-detect-at-boot fix, wifi auto-recovery, overlayfs/
  read-only-root for power-loss resilience, magenta auto-trigger. See `docs/IDEAS.md`.
- **Satellite expansion:** a bidirectional USB-serial link to an Adafruit Circuit
  Playground Express for volume input + a secondary status display + a NeoPixel VU
  meter (lightweight Pi-side level envelope, not FFT). The Pi Zero 2 W's single USB
  data port is the constraint; an LM3915 analog VU ladder is a separate optional
  hardware project. Requested kid features: **repeat-track** (Nathan) and a now-playing
  LCD. See `docs/IDEAS.md`.

## Tests
Pure-logic unit tests (no Pi/hardware needed — run on any machine):
```bash
python3 -m unittest discover -s tests        # or: pytest tests/
```
They cover the gesture state machine, LED rendering + shutdown latch, the Mopidy
JSON-RPC client, autoplay, and the YouTube auth converter — including regression
guards for the bugs we hit (next-on-paused→silence, single-vs-double, SAPISIDHASH).

## Docs map
- `docs/SETUP_PLAYLIST_AND_COOKIES.html` — the **hand-to-a-kid** interactive setup guide
  (playlist + cookies; open in a browser)
- `docs/WORKLOG.md` — full chronological build narrative + gotchas (read this if stuck)
- `docs/POWER-LOSS.md` — safe-shutdown + overlayfs resilience
- `docs/ROBUSTNESS-NOTES.md` — tests + the robustness pass
- `docs/STEP0-NOTES.md … STEP5-NOTES.md` — per-step detail
- `docs/IDEAS.md` — backlog / deferred robustness / future features
- `docs/PLAN.md` — the build plan
- `drivers/` — prebuilt AIY DKMS debs + the source patches
