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

## 2. jack-detect at boot — TBD
(plug-present-at-boot reads "empty" until replug; see docs/IDEAS.md for candidates.)

## 3. wifi auto-recovery — TBD
(verify controller blue-on-wifi-down and recovery to green/amber.)

## 4. power-loss resilience (overlayfs) — TBD
(actionable README procedure; deferred implementation.)

## 5. magenta auth-expiry auto-trigger — TBD
