# Walkman — Step 2 notes (boot-to-music)

**Status: ✅ Step 2 PASSED 2026-06-07.** Cold reboot → device auto-starts Mopidy and
shuffle-plays the configured playlist with zero interaction (verified: both services
active, autoplay `Result=success`, `state=playing`).

## What was built
- **`config/walkman.toml`** — per-device user settings (playlist id, random/repeat/
  consume, mopidy RPC url). Swap this per unit.
- **`src/walkman/mopidy_client.py`** — tiny stdlib-only HTTP JSON-RPC client (+ a
  `wait_until_ready` poll). Reused by the controller later.
- **`src/walkman/autoplay.py`** — waits for Mopidy + network, loads the playlist,
  sets shuffle/repeat/consume, plays; retry/backoff for wifi/yt-dlp warm-up.
- **`systemd/walkman-mopidy.service`** — Mopidy as `User=brew` (reuses the verified
  config/auth/audio), `After=network-online`, `Restart=always`.
- **`systemd/walkman-autoplay.service`** — oneshot, `Requires/After` mopidy +
  network, runs `autoplay.py`.

## Deploy (current, manual — will be folded into setup.sh)
- Code on Pi at `/home/brew/walkman/{src,config}`; units in `/etc/systemd/system/`.
- `sudo systemctl enable --now walkman-mopidy walkman-autoplay`.
- Stock `mopidy.service` left disabled (avoids port 6680 conflict).

## Decisions / notes
- **Run as `brew`, not the `mopidy` user:** reuses everything proven in Step 1
  (config at `~/.config/mopidy/mopidy.conf`, auth at `~/.config/walkman/`,
  audio-group access). Lowest risk for the appliance; per-device swap still works.
- **Volume default lowered −6 dB** (Speaker 39→35 = +6 dB; Headphone 30→26 = −7.5 dB),
  persisted via `alsactl store`.

## Headphone auto-switch (added 2026-06-07, verified)
- **`src/walkman/jack_monitor.py`** + **`systemd/walkman-jack.service`** (runs as root):
  watches the bonnet's Headphone Jack input (`SW_HEADPHONE_INSERT`) and flips
  `Speaker Switch` — headphones in → speaker OFF; out → speaker ON. Event-driven
  (blocks on `/dev/input/event*`), stdlib only, finds the jack device by name
  (index-independent), sets initial state via `evtest --query`.
- Verified live both directions: unplug → speaker ON, plug in → speaker OFF.
  (Confirmed the empty-jack state reads correctly — speaker on.)
- Default volume lowered a second −6 dB on 2026-06-07: Speaker 31 (0 dB),
  Headphone 22 (−13.5 dB), persisted.

## Carry-forward / hardening
- **yt-dlp JS-runtime warning:** "No supported JavaScript runtime… some formats may
  be missing." Streaming works, but installing a JS runtime (e.g. `deno`) would make
  YouTube extraction more robust — add to hardening.
- Autoplay's first track wasn't obviously randomized across two boots (got the same
  track) — verify `set_random` reshuffles the start; minor.
- Later: controller may subsume autoplay; speaker↔headphone auto-switch; move to
  proper per-device system paths if we ever switch off `User=brew`.
