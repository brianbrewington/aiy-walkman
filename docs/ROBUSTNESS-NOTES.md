# Walkman — robustness pass notes

The hardening pass after the device was functionally complete. Documented as we go.

## 1. Unit tests (done, 2026-06-07)
`tests/` — stdlib `unittest` (runs under `python3 -m unittest discover -s tests` and
`pytest`). 33 tests, all green. Pure logic, no Pi/hardware. Coverage:
- `test_press_pattern` — single/double/long/triple/spaced, and long-press suppresses a
  pending click (regression for the gesture machine).
- `test_led` — mode→RGB (green breathes, amber steady, blink phases, shutdown=white),
  and `force_mode` latch (regression for the white-flash-to-green shutdown bug).
- `test_mopidy_client` — JSON-RPC payload/result/error/transport handling, `is_ready`.
- `test_controller` — gesture dispatch; **double = next + play** (regression for
  next-on-paused→silence); long = latch white + poweroff (subprocess mocked).
- `test_autoplay` — load→set modes→play; bail when no tracks resolve.
- `test_ytmusic_auth` — cURL/cookies.txt parse, SAPISIDHASH build, drops stale
  authorization, `$'...'` quoting (regressions for the auth saga).

## 2. jack-detect at boot — accepted limitation (no clean fix)
Probed for a re-detect hook: the codec exposes only a **read-only** `Headphone Jack`
kcontrol and no writable jack/detect/rescan control, so there's no software way to
force the RT5645 to re-evaluate a plug that was present at codec-init. Live
insert/remove works perfectly; only the present-at-boot initial condition is stale.
**Decision: accept + document.** Workaround = replug once if you boot with headphones
already in (kids usually power on first, then add headphones). Logged in docs/IDEAS.md.

## 3. wifi auto-recovery — verified (mechanism)
The controller's `_wifi_ok()` uses `nmcli -t networking connectivity` (returns `full`
on the Pi); not-`full` → blue blink; restored → green/amber. Logic is unit-tested via
the LED render tests and the status-loop mapping. A live wifi-drop test wasn't run (it
would sever our SSH); the mechanism is confirmed and recovery is automatic (the poller
re-reads every cycle).

## 4. power-loss resilience — done (docs/POWER-LOSS.md)
Two layers: the **safe-shutdown gesture** (long-press → white → clean poweroff, already
built) and **read-only root via overlayfs** (actionable raspi-config procedure, what
breaks while read-only, the disable→reauth→enable cycle, and the cookie-expiry
interaction). Implementation (enabling overlayfs) is left to the operator per unit.

## 5. magenta auth-expiry auto-trigger — deferred (rationale)
Reliably distinguishing "cookie expired" from a transient "PO-token track stall" is
hard (both surface as playback that won't start). A false magenta would cry wolf. The
**LED capability exists** (`led.REAUTH`) and the cookie-monster is run manually for now.
Revisit with a dedicated ytmusicapi auth-probe (cheap authenticated call → 401/403 =
expired) if manual re-auth proves annoying.

## Status
Robustness pass: unit tests ✅, wifi mechanism ✅, power-loss docs ✅; jack-boot &
magenta-auto = accepted/deferred with documented rationale. Next: cookie-monster
end-to-end check (user-run, needs a live cookies.txt).
