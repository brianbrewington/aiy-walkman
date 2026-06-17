# Walkman — Step 5 notes (reproducibility, provisioning & re-auth)

> **Historical checkpoint.** This records the first reproducibility pass. The current
> source of truth is [`../README.md`](../README.md), `setup.sh`, and
> `scripts/walkman-account.sh`; some counts below predate the CPX satellite.

**Status: implemented, with later CPX additions.** Original goal: make a *second*
unit real and recover from cookie expiry, by capturing the by-hand build into an
idempotent installer + a single per-device account step (which doubles as the
cookie-monster). Robustness deferred.

## What was built

### `setup.sh` — idempotent, account-agnostic system install
Run on the Pi from the repo at `/home/brew/walkman`: `sudo ./setup.sh`. Steps (each
guarded so re-runs are clean no-ops):
1. apt (build/dkms tooling, mopidy + gstreamer plugins, alsa-utils, evtest, etc.).
2. AIY DKMS drivers from `drivers/prebuilt/*.deb` — **guard is a filesystem check**
   (`/var/lib/dkms/aiy` …), not `dkms status`, because `dkms` isn't reliably on root's
   PATH under `sudo` (that bit us on the first run). DKMS source debs rebuild per-kernel.
3. config.txt: comment `dtparam=audio=on` (backup first).
4. deno → `/usr/local/bin/deno` (arm64 release; skip if present).
5. pip pinned: `Mopidy-YouTube==4.0.2 ytmusicapi==1.12.1 yt-dlp[default]==2026.3.17`,
   **then `pip uninstall brotli`** (it gets re-pulled by `[default]` every run, so the
   install→uninstall sequence is deliberate and idempotent-by-result).
6. `mopidy.conf` → `~/.config/mopidy/` (cp -n, never clobber).
7. systemd: install + enable the `walkman-*` units; keep stock `mopidy.service` off;
   install a journald retention cap for predictable log rotation.
8. CPX satellite additions now load `snd-aloop`, install the CPX udev rule, and enable
   `walkman-satellite.service`.
9. ALSA baseline: install `config/asound.state` → `/var/lib/alsa/asound.state` +
   `alsactl restore` (the exact RT5645 routing + channel switches + calm volumes,
   captured from the working dev unit — 1540 lines).

**Gotcha found during testing:** the dev unit's `/home/brew/walkman` was a *partial*
scp copy, missing `drivers/` and `systemd/`, so steps 2 & 7 failed with "No such file".
A fresh unit gets these from `git clone`; the install assumes the **full repo** at
`/home/brew/walkman`.

### `scripts/walkman-account.sh` — per-device account (provisioning AND cookie-monster)
One script, two uses (the shared process):
- `--cookies <file>` → reuses `scripts/ytmusic_auth_from_curl.py` to write
  `~/.config/walkman/ytmusic-auth.json` (mode 600, with the SAPISIDHASH header so
  ytmusicapi treats it as BROWSER auth).
- `--playlist <id>` → updates `config/walkman.toml` (sed on the `id = "..."` line).
- `--hostname <name>` → `hostnamectl set-hostname` (provisioning only).
- Then restarts `walkman-mopidy` + `walkman-autoplay`.
- **Provisioning** = all flags; **re-auth (cookie-monster)** = just `--cookies`.
Mechanics verified locally (dummy cookie → correct 8-header auth JSON incl.
`authorization`; playlist sed updates the id). Full cookie→restart→playback is the
real-world cookie-monster run (needs a live cookies.txt).

### Docs
- `README.md` rewritten: install runbook, per-device account + re-auth one-liner,
  services table, LED meanings (incl. magenta), known limitations, roadmap.
- `docs/WORKLOG.md` hardened by subagent (567 → 933 lines): copy-pasteable commands,
  diagnostics toolbox, symptom→cause→fix table, external links, dated "this will rot"
  warning, prereqs + repo→Pi path map, and honesty markers (brotli = suspected /
  workaround-not-root-fix; ios client never benchmarked; etc.).
- `config/asound.state` added (the mixer baseline setup.sh restores).

## Provisioning model (chosen: setup.sh primary + optional image)
Fresh unit: flash RPi OS Lite 64-bit Bookworm (rpi-imager: hostname/user/wifi/SSH) →
clone repo to `/home/brew/walkman` → `sudo ./setup.sh` → reboot → `walkman-account.sh
--cookies … --playlist … --hostname …`. Optional: `dd` a golden image after one unit
is done; new unit = flash image + the account step only.

## Deferred to a later robustness pass (in docs/IDEAS.md)
jack-detect-at-boot fix; wifi auto-recovery verification; overlayfs/read-only-root for
power-loss; magenta auto-trigger (LED capability already exists).
