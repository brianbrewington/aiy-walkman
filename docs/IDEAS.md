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
  - a control on the future CPX satellite, or
  - default off; toggled via the satellite/secondary input.
  Likely lands as a **CPX control** to avoid overloading the one button.

- **Now-playing LCD screen ("Phase 3")** — a small LCD/OLED showing current track
  title/artist. Fits the **secondary status display** role already planned for the
  CPX satellite (driven over the same USB-serial link — the Pi already knows the
  track via Mopidy's JSON-RPC `core.playback.get_current_track`). An I2C OLED
  directly on the bonnet's spare I2C is also possible, but the bonnet's headers are
  occupied; the CPX/serial route keeps the main unit's single-cable simplicity.
  See the satellite-expansion hook in `PLAN.md`.

## Operations / provisioning (planned — Step 5+)

- **"Cookie-monster" — kid-runnable re-auth.** When the LED goes **magenta** (cookie
  auth expired), a child should be able to refresh YouTube Music auth with minimal
  fuss. Today it's: export cookies.txt (browser extension) → run
  `scripts/ytmusic_auth_from_curl.py` → scp to the Pi. That's not kid-friendly.
  Want a simple, guided refresh flow (e.g. a tiny local web page on the device, or a
  one-command wrapper) that takes a fresh cookie and drops it in the right place +
  restarts Mopidy. Ties to the magenta "needs re-auth" LED state.
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

The install guide (`docs/INSTALL.html`) now takes the box username as a field, so the
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

## Notes
- These reinforce the value of the **pluggable input layer + bidirectional CPX
  serial channel** already in the design: volume, repeat-track, and a now-playing
  display can all attach there without touching the core controller.
