# Walkman — feature backlog / future ideas

Captured ideas beyond the core build (Steps 0–5). Not committed to a timeline.

## Requested by the family (2026-06-06 — first battery demo, family approved! 😄)

- **Repeat-track ("play this song again")** — *requested by Nathan (12).*
  Good news: this is **built into Mopidy core** — `core.tracklist.set_single(true)`
  + `set_repeat(true)` repeats the current track. So it does NOT necessarily need the
  CPX. Options for how to trigger it:
  - a button gesture on the main button (but the single button is already full:
    single=play/pause, double=next, long=shutdown — would need a triple-press or a
    press-and-hold variant), or
  - a control on the CPX satellite, or
  - default off; toggled via the satellite/secondary input.
  Likely lands as a **CPX control** to avoid overloading the one button.

- **Now-playing LCD screen ("Phase 3")** — a small LCD/OLED showing current track
  title/artist. Fits the **secondary status display** role on the CPX/serial path
  (driven over the same USB-serial link — the Pi already knows the
  track via Mopidy's JSON-RPC `core.playback.get_current_track`). An I2C OLED
  directly on the bonnet's spare I2C is also possible, but the bonnet's headers are
  occupied; the CPX/serial route keeps the main unit's single-cable simplicity.
  See the satellite-expansion hook in `PLAN.md`.

## Operations / provisioning (planned — Step 5+)

- **"Cookie-monster" — kid-runnable re-auth.** When the box needs login help, a child
  should be able to refresh YouTube Music auth with minimal fuss. Today it's: export
  cookies.txt (browser extension) → run
  `scripts/ytmusic_auth_from_curl.py` → scp to the Pi. That's not kid-friendly.
  Want a simple, guided refresh flow (e.g. a tiny local web page on the device, or a
  one-command wrapper) that takes a fresh cookie and drops it in the right place +
  restarts Mopidy. Ties to the magenta "needs re-auth" LED state.
- **Detect a present-but-DEAD cookie → magenta.** Today `decide_mode` only checks
  whether the auth *file exists*; an expired/invalidated cookie (file present, but
  YouTube returns `logged_in: 0`) shows amber/red, not magenta — so the device can't
  tell the kid "log me in again." Add a lightweight auth-validity probe (e.g. a cheap
  authenticated ytmusicapi call, or watch Mopidy for repeated `logged_in: 0` /
  `Cannot load` playlist errors) and drive the **magenta** "needs re-auth" state from
  it. Discovered in field test #1 (WORKLOG §13): cookies die fast when the source
  browser keeps rotating `__Secure-*PSIDTS` (e.g. multiple open YouTube tabs); the
  durable mitigation is **export in Incognito then close it** + prefer a personal
  (non-Workspace) account, now documented in the setup guide + README.
- **Bulletproof new-unit bring-up from scratch.** Decide the provisioning model:
  either (a) capture a golden **SD-card image** (everything installed) that's flashed
  per unit, then a tiny one-time per-device step (drop in that account's oauth/cookie
  file, set playlist id in walkman.toml, set hostname); or (b) a fully idempotent
  `setup.sh` run on a fresh Raspberry Pi OS Lite that installs everything (incl. the
  AIY DKMS drivers, deno, yt-dlp[default]-minus-brotli, the shim, services). Image =
  fast/reliable but heavier to maintain; setup.sh = lighter/versioned but more steps
  and exposed to upstream drift. Likely: setup.sh as source of truth + an image as a
  convenience snapshot.

## Known limitation — jack detection at boot (Step 5 hardening)

**Symptom:** if the device boots/reboots with a plug **already in** the 3.5mm jack,
the RT5645 jack-detect reads **"empty"** (edge-driven detection misses a
plug-present-at-init), so audio mis-routes to the **speaker** until you physically
unplug + replug (which generates a fresh insert edge that the auto-switch catches
correctly). Live insert/remove works perfectly; only the initial condition is wrong.
(Verified 2026-06-07: plug was in across reboots, `evtest --query` read empty; a
pull+reinsert immediately flipped to inserted and routed to headphones.)

This is a hardware/driver quirk, not our state machine — `jack_monitor` *does* query
the initial state; the hardware just reports it stale.

Candidate fixes to try (need a reboot-with-plug-in test to validate):
- Read the `iface=CARD,name='Headphone Jack'` kcontrol at startup instead of/along
  with the input SW (it tracks state — unknown if it's reliable at boot; likely same
  limitation since same source).
- Force the codec to re-run jack detection at startup (RT5645 jd register poke / re-
  trigger), so a present-at-boot plug is evaluated.
- Accept + document: if booted with headphones in, replug once. (Lowest effort;
  acceptable if kids usually power on with nothing plugged, then add headphones.)

## Full username genericity (beyond the install guide)

The install guide (`docs/SETUP_PLAYLIST_AND_COOKIES.html`) now takes the box username as a field, so the
`scp`/`ssh` commands aren't hardcoded to `brew`. But the rest of the system still
assumes `brew`:
- `setup.sh` honors a `WALKMAN_USER` env var but defaults to `brew`, and warns if the
  repo isn't at `/home/brew/walkman`.
- The `systemd/*.service` units hardcode `User=brew`, `/home/brew/...` paths, and the
  shim `PYTHONPATH=/home/brew/walkman/shim`.
- `mopidy.conf` / `walkman.toml` use `/home/brew/...` auth paths.
To support a different login on a fresh unit, template these (e.g. setup.sh rewrites
the unit files + configs from `WALKMAN_USER`/`$HOME`). Low priority while all units use
`brew`, but needed for a truly generic image.

## CPX two-sided meter — delay auto-calibration (cross-correlation)

The two-sided meter's lights run ahead of the buffered speaker output; `meter_sync_delay_ms`
compensates with a fixed delay line, tuned by eye against a sparse bass-click probe. A
rigorous auto-calibrator: play a log chirp / MLS, capture on the CPX mic, cross-correlate
(GCC-PHAT) for the lag, auto-write `meter_sync_delay_ms`. Caveats: the CPX mic's ADC latency
is a fixed offset (characterize + subtract); Pi↔CPX clocks aren't synced (trigger + timestamp
carefully); the YT/codec latency is **common-mode** (upstream of the GStreamer tee) so it
cancels in the tap-vs-speaker offset. No-mic alternative: read the speaker `alsasink` buffer
latency from GStreamer directly. (Phase 2 — manual tuning works for now.)

## From the Codex/Cursor repo audit (2026-06-18) — deferred items

Fixed on the spot: ButtonSource GC ref, shutdown-gesture suppression, volume re-clamp on the
account-script Mopidy restart, autoplay config guard, playlist-id validation, README clone
URL, CPX runbook staging. Deferred:
- **Button callback blocks on Mopidy RPC** (controller): a stalled RPC can hang the gpiozero
  callback up to the client timeout. Proper fix: dispatch actions to a worker queue/thread.
- **Auth LED by file-existence, not validity** — same root as the magenta-on-dead-cookie gap above.
- **CPX serial fallback picks the last match**: the udev `/dev/walkman-cpx` symlink is the
  deterministic production path; the `comports()` fallback should fail loudly on ambiguity.
- **deno install**: version is pinned now; add a sha256 check for full supply-chain integrity.
- **Test coverage**: jack_monitor (discovery/parse/apply), shim idempotency, a boot/restart
  integration smoke (service order + volume-cap invariant), a walkman-account.sh harness.
  Refresh stale test counts in docs; add CONTRIBUTING/license.

## Notes
- These reinforce the value of the **bidirectional CPX serial channel** now in the
  build: repeat-track and a now-playing display can attach there without touching
  the core controller.
