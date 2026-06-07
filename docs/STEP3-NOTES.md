# Walkman — Step 3 notes (button gestures) + YouTube reliability

**Status: Step 3 button gestures working & user-confirmed 2026-06-07.**
- **single press** → play/pause toggle ✓
- **double press** → next track (also resumes if paused) ✓
- **long press (≥1.2 s)** → safe shutdown (white LED cue, then `systemctl poweroff`) —
  code complete, not yet user-tested (it powers the Pi off).

## What was built
- `src/walkman/button.py` — `PressPattern` (pure state machine, unit-tested in
  `tests/test_press_pattern.py`) + `ButtonSource` (gpiozero Button on GPIO23,
  `hold_time=1.2`, `pull_up`). long via gpiozero `when_held`; single/double via a
  release + double-window timer.
- `src/walkman/led.py` — KTD2026 sysfs control (set_rgb/off; `shutdown_cue()` = white).
- `src/walkman/controller.py` — runs as root; maps gestures → Mopidy actions.
  `next` calls `core.playback.next` **then `play`** (next on a paused player stays
  paused → silence; this guarantees the next track is audible).
- `config/walkman.toml` `[button]` — gpio, long_press_seconds, double_click_window.
- `systemd/walkman-controller.service` (User=root, After mopidy). Enabled.

UX note: arcade button has travel — a natural double-tap sometimes registered as two
singles; firmer/quicker double-tap works. `double_click_window_seconds` (0.35) can be
widened in `walkman.toml` if needed.

## The YouTube extraction reliability saga (important context)

Chasing `next` surfaced that **`next`/track-switching was unreliable** — root cause is
**YouTube's anti-bot (signature/nsig solving + PO tokens)**, not our code. The path:

1. Without a JS runtime, most tracks won't resolve ("not playable") → `next` can't advance.
2. Installed **deno** (yt-dlp's JS runtime) + **`yt-dlp[default]`** (pulls `yt-dlp-ejs`
   challenge-solver scripts) → fixed signature-solving…
3. …but that pulled in **`brotli` 1.2.0**, whose C-extension **SEGFAULTs in Mopidy's
   worker threads** on this ARM Pi (faulthandler backtrace: `_brotli` in
   `urllib3/response.py decompress`). → **Removed brotli** (`pip uninstall brotli`);
   urllib3 falls back to gzip. Segfault gone, Mopidy stable.
4. Underlying wall: **PO-token enforcement**. Client tradeoffs on this Pi Zero:
   - `android_vr`: fast, no JS-solving, but only ~1/6 tracks resolve (PO-token blocked)
   - `web` + deno: resolves reliably but ~20–30 s/track (JS on a weak CPU)
   - **both (chosen)**: best coverage; fast android path, slow web fallback.
5. Forced the client via a **shim** (`shim/sitecustomize.py`, loaded into Mopidy via
   `PYTHONPATH` in `walkman-mopidy.service`) — monkeypatches `yt_dlp.YoutubeDL` to set
   `extractor_args youtube.player_client = ["android_vr","web"]`. Does NOT edit the
   Mopidy-YouTube package; reversible; survives upgrades.

**Net:** continuous playback works well in practice (user listened for an hour;
auto-advance is helped by prefetch). Manual `next` occasionally stalls on an
unresolvable track (press again). The robust+fast fix, if ever wanted, is a
**PO-token provider** (`bgutil-ytdlp-pot-provider`, a node/deno sidecar) — the
lightweight version of "run a real browser." A headless browser would also work but
is impractical on a 512 MB Pi Zero 2 W (RAM/CPU/Widevine); it'd suit a Pi 4/5.

## On-Pi changes to fold into setup.sh
- `deno` installed to `/usr/local/bin/deno` (arm64 release).
- `pip install yt-dlp[default]` (for yt-dlp-ejs) **then `pip uninstall brotli`** (the crasher).
- The shim + `PYTHONPATH` in walkman-mopidy.service.
