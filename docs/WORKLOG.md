# Walkman — Engineering Worklog

> An honest, chronological build log: every yak-shave, dead end, root cause, and
> fix. This is deliberately *not* a sanitized README. If you are the future
> maintainer (or me, six months from now, staring at a silent jack or a segfaulting
> Mopidy), read the **"Gotchas & hard-won lessons"** section first, then come back
> here for the story behind each decision.
>
> Build dates: **2026-06-06** (Step 0 + Step 1) and **2026-06-07** (Steps 2–3 +
> headphone auto-switch + the YouTube reliability saga).

---

## 1. Overview — what this thing is

**Walkman** is a screen-free, one-button YouTube Music player for kids. Boot it and
it shuffle-plays one configured playlist through the bonnet's audio; the only I/O is
a single arcade button and its built-in RGB LED.

**Hardware**

- **Raspberry Pi Zero 2 W** — quad-core ARM, but only **512 MB RAM** (~270 MB free
  at the start of the build). This number drives almost every architecture decision:
  one combined controller process instead of three, stdlib-only Python clients, no
  headless browser, no FFT VU meter.
- **Google AIY Voice Bonnet.** The real hardware (verified by probing, *not* the
  brief):
  - Codec at I2C `0x1a` is a **Realtek RT5645** — a *real* codec, not the v1 AIY
    HAT's dummy "voicehat" codec.
  - AIY IO MCU at I2C `0x52` (`google,aiy-io-i2c`) provides the `aiy-gpio` supplier
    that *both* the sound card and the LED depend on.
  - Button RGB LED is a **KTD2026** at I2C `0x31`.
  - The bonnet **EEPROM** auto-declares the device-tree nodes (`rt5645@1a`,
    `aiy-io-i2c@52`, `ktd2026@31`) — so no manual `dtoverlay` line is needed once the
    drivers exist.

**OS / kernel**

- Raspberry Pi OS Lite 64-bit (Bookworm), kernel **`6.12.87+rpt-rpi-v8`**.
- NetworkManager is the net stack (`nmcli`).

**How the build was driven**

- Authored on the Mac at `/Users/brew/Code/walkman`, deployed to the Pi over
  passwordless SSH to `brew@walkman-a.local` (ed25519 key), with a tmux session
  holding a persistent Pi shell. All hardware commands ran on the Pi.

**Constraints that shaped everything**

- 512 MB RAM → lightweight everything.
- Kids will **yank power** → safe-shutdown gesture now; read-only-root documented for
  later.
- Auth to YouTube Music is the single most fragile dependency (unofficial APIs that
  break periodically) → built a re-auth tool and pinned versions.

---

## 2. The plan vs. reality

The original `PLAN.md` was grounded in live probing, but two of its core premises
turned out **wrong** once we dug in — worth flagging up front so the plan and this
log don't confuse a future reader:

1. **PLAN assumed LED control would be raw I2C via `smbus2`** because "no kernel
   driver exists." We *did* end up building the `leds-ktd202x` DKMS driver (Step 0),
   so production LED control is via `/sys/class/leds`, not raw I2C. Raw I2C is now
   only a documented fallback (and is *blocked* while the driver is bound — see
   gotchas).
2. **PLAN assumed audio just needed the `googlevoicehat-soundcard` overlay** (the
   `.dtbo` and module were present on disk). That overlay is for the **v1 HAT** and
   does nothing for this RT5645 bonnet. Real fix was building the AIY DKMS driver
   stack.

The `PLAN.md` text still reflects the original assumptions in places; trust this
worklog and the STEP notes for what actually happened.

---

## 3. Step 0 — Audio + LED foundations (2026-06-06)

**Goal:** prove the two genuinely-uncertain foundations (can we make sound? can we
drive the LED?) on real hardware before writing a line of app logic. Hard gate.

### 3.1 The "no driver to bind" trap

**What we hit.** `aplay -l` showed only `vc4hdmi`. `/sys/class/leds` had no bonnet
entry. The brief's framing suggested a regulator/supplier dependency was *blocking*
the driver from binding (a `waiting_for_supplier` story).

**Root cause.** There was nothing to block. The stock Bookworm kernel
(`6.12.87+rpt-rpi-v8`) ships **none** of the AIY drivers — not the codec, not the AIY
IO MCU driver, not the LED driver. `waiting_for_supplier` read `0`; dmesg showed no
deferred probe. There was simply no driver present. Everything downstream
(sound card *and* LED) depends on the `aiy-gpio` supplier that the AIY IO MCU driver
provides, so until that exists, nothing comes up.

**Resolution.** Build the AIY driver stack from a community fork that ported Google's
official AIY drivers to kernel 6.12:

- Fork: **`HorseyofCoursey/trixie-aiyprojects`**
- **Pinned commit: `4ee62cedb2b0756e3503a11585a7064f7edd0871`** (2026-04-05).
- Lineage is a fork-of-fork: google → viraniac → HorseyofCoursey. Reviewed: the
  changes are mechanical kernel-6.12 API fixes over Google's originals. No
  network/file/usermode calls in the drivers; the lone `request_firmware_direct` is
  an admin-only sysfs MCU-flash path we don't use. The `rt5645.c.patch` is mainline
  codec + small jack-detect tweaks.

Three DKMS packages, built with `dpkg-buildpackage -b -us -uc` and installed
**aiy first** (it provides the `aiy-gpio` supplier the others need):

- `aiy-dkms` → `aiy-io-i2c` (+ `gpio/pwm/adc-aiy-io`)
- `aiy-voicebonnet-soundcard-dkms` → `rt5645`, `rl6231`, `snd-aiy-voicebonnet`
  (+ ALSA UCM2 under `/usr/share/alsa/ucm2/aiy-voicebonnet/`)
- `leds-ktd202x-dkms` → the KTD2026 LED driver

Checkpointed source debs live in `drivers/prebuilt/`. Source clone on the Pi:
`~/walkman-build/aiy-src` (checked out at the pinned commit).

### 3.2 Two local patches the fork still needed

These **must** be carried into any future `setup.sh`:

1. **LED probe signature** — `drivers/patches/leds-ktd202x-probe-6.12.patch`. The
   fork missed the i2c `.probe` signature change for kernel 6.6+ (only in the LED
   file). Drop the `const struct i2c_device_id *id` arg and use `client->name`
   instead of `id->name`. Without this, `leds-ktd202x` won't compile.

2. **Dependency name fix** — `drivers/patches/control-deps.md`. All three
   `debian/control` files declare `Depends: raspberrypi-kernel-headers`, which is a
   **stale 6.1-era** package name. On this OS the headers package is
   `linux-headers-rpi-v8` (Pi 5 = `linux-headers-rpi-2712`). Installing as-is fails
   the dependency and **leaves apt in a broken state.** Fix (sed with a non-`|`
   delimiter like `@`):
   ```
   Depends: linux-headers-rpi-v8 | linux-headers-rpi-2712 | raspberrypi-kernel-headers, dkms (>= 1.95), ${misc:Depends}
   ```
   The prebuilt debs already have this baked in.

### 3.3 config.txt

- **Commented out `dtparam=audio=on`** (disable built-in bcm2835 audio) — backup at
  `/boot/firmware/config.txt.bak-walkman`.
- Removed an earlier *wrong* `dtoverlay=googlevoicehat-soundcard` line (v1-HAT
  overlay; does nothing here).
- **No bonnet overlay line needed** — the EEPROM provides the DT nodes; the DKMS
  drivers bind to them.

### 3.4 The audio gotcha that cost hours — the silent 3.5mm jack

**Goal:** audible tone from the bonnet's 3.5mm jack.

**What we hit.** The card came up (`card 1: aiyvoicebonnet`), the internal speaker
worked, DAPM showed the headphone path fully powered ("HP amp On", HPOL/HPOR On) —
and the **3.5mm jack was dead silent.** Everything looked right.

**Root cause.** The bonnet's own UCM `EnableSequence` turns on **`Speaker Channel
Switch`** but **not** `Headphone Channel Switch`. So the HP analog path is powered but
the channel mute is left engaged → silence. This is a UCM bug/omission, not a wiring
or routing problem, which is exactly why it ate so much time: every diagnostic
pointed at a healthy path.

**Resolution.** Enable **`Headphone Channel Switch`** in the mixer. Baseline saved
with `sudo alsactl store` (restored on boot via `alsa-restore`):

- `Speaker Switch` on, `Speaker Channel Switch` on, `Speaker Playback Volume` 45
- `Headphone Switch` on, `Headphone Channel Switch` on, `Headphone Playback Volume` 30
- `DAC1 Playback Volume` 60,60
- plus the RT5645 HP/SPK routing from the UCM HiFi `EnableSequence`.

> Lesson: with this codec, "the path is powered" ≠ "the path is unmuted." Check
> *both* the `*Switch` and `*Channel Switch` controls for whichever output you want.

### 3.5 Jack detection works — but the machine driver doesn't auto-mute

- Jack detect is wired: input device `event2` = "aiy-voicebonnet Headphone Jack".
  Query state with `sudo evtest --query /dev/input/event2 EV_SW SW_HEADPHONE_INSERT`
  → exit **10 = inserted**, **0 = not**.
- **But:** the RT5645 machine driver only *powers the headphone pin* on insert; it
  does **not** mute the speaker. The "speaker low by default, headphones override"
  behavior is ours to implement (we did, on Step 2 day — see §6).

### 3.6 LED bring-up

- Bound driver path: `/sys/class/leds/ktd202x:led1..4`, write `0–255` to
  `brightness` (needs root).
- **Color map (pinned by probing): led1 = red, led2 = green, led3 = blue. led4
  unused** (KTD2026 is a 3-channel part). Amber = led1≈200 + led2≈120.
- **Raw I2C is now blocked.** Once the kernel driver binds, plain i2c to `0x31`
  returns "Device or resource busy." Only `i2cget -y -f 1 0x31 ...` (forced) reads,
  and it contends with the driver. To actually use raw I2C you'd have to unload
  `leds_ktd202x` first. The bound driver is the production path; raw I2C is a
  documented fallback only.

### 3.7 Step 0 result

Card auto-loads after reboot, LED driver present, modules auto-load, mixer state
restored, tone audible from **both** outputs (user-confirmed by ear). Committed and
pushed to the private GitHub repo. Full detail: `docs/STEP0-NOTES.md`.

---

## 4. Step 1 — Mopidy + YouTube Music (2026-06-06)

**Goal:** a full track from the user's actual YouTube Music subscription streams
end-to-end through the bonnet. Hard gate.

### 4.1 Picking the extension

- Installed **Mopidy core 3.4.1 + GStreamer 1.22** via apt.
- The dedicated **`Mopidy-YTMusic` is archived/stale** → chose **natumbri's
  `Mopidy-YouTube`** (actively maintained, uses `yt-dlp`).
- **Pinned working versions** (these track an unofficial API and break periodically —
  upgrade them as a *unit* and re-test):
  - `Mopidy-YouTube == 4.0.2`
  - `ytmusicapi == 1.12.1`
  - `yt-dlp == 2026.3.17`
  - installed via `pip --break-system-packages`.

### 4.2 The auth saga

**Dead end #1 — durable OAuth.** The original plan wanted per-device OAuth
refresh-token auth (durable, no silent cookie expiry). **Mopidy-YouTube 4.0.2 does
not support it** — it calls `YTMusic(auth=file)` with no `oauth_credentials`. As the
plan explicitly allowed, we fell back to **browser/header (cookie) auth.** The only
working auth input in 4.0.2 is `musicapi_browser_authentication_file` (a JSON dict of
request headers including the auth cookie). `musicapi_cookiefile` is
**non-functional / commented out** in this version — don't waste time on it.

**Dead end #2 — Chrome "Copy as cURL" redacts the cookie.** The natural way to grab a
header set is DevTools → Copy as cURL on a `music.youtube.com` request. Recent Chrome
**REDACTS the cookie** from the copied cURL. Confirmed dead end.

**Resolution — cookies.txt route.** Installed the open-source **"Get cookies.txt
LOCALLY"** extension, export a Netscape `cookies.txt`, and convert it. Wrote
`scripts/ytmusic_auth_from_curl.py` to accept *either* a cURL paste *or* a
cookies.txt (auto-detected on stdin), printing only safe diagnostics (header names,
cookie length, booleans) — never secrets.

**The gotcha that cost the most time here.** ytmusicapi 1.12 only classifies an auth
file as **BROWSER** auth if it has an **`authorization` header containing
`SAPISIDHASH...`** sitting next to the `cookie`. `is_browser` needs *both*;
`determine_auth_type` keys off the `SAPISIDHASH` string. Without it, ytmusicapi
assumes the file is OAuth and errors with "oauth JSON provided … oauth_credentials
not provided" — a totally misleading message when your problem is a missing header.

The kicker: ytmusicapi **recomputes** that hash per request, so the value's freshness
is irrelevant — the file just needs *one present at generation time* to be classified
correctly. So the converter computes a valid `SAPISIDHASH` from the cookie's SAPISID
(same SHA1-of `"<ts> <sapisid> <origin>"` algorithm ytmusicapi uses) and injects it,
plus `origin`/`x-origin`. See `get_authorization()` in the script.

**Re-auth procedure** (when cookie auth expires): export cookies.txt → run the
converter → scp to the Pi → restart Mopidy. Documented in `docs/STEP1-PROGRESS.md`.

**Housekeeping:** a loose `copied_as_curl.txt` (containing a real cookie) was found
in the repo dir, git-excluded, and deleted; auth-secret patterns are in
`.gitignore`.

### 4.3 Step 1 result

**✅ PASSED 2026-06-06.** Playlist `PL5bKS0Bw-MfRrtcFhf0SQeo-evkVM8Wgx` loaded 24
tracks, a real track ("More To This" — Marc Scibilia) played through the bonnet,
position advancing, user confirmed audio. Shuffle + repeat on, consume off.

> Side annoyance noted during this step: the user's own SSH sessions to the Pi kept
> dropping and large terminal pastes truncated — hence the clipboard/file-based auth
> workflow and a suggested `ServerAliveInterval 30` in `~/.ssh/config`.

---

## 5. Step 2 — Boot-to-music (2026-06-07)

**Goal:** cold reboot → device auto-plays the playlist, zero interaction.

### 5.1 What was built

- **`config/walkman.toml`** — per-device settings (playlist id, random/repeat/
  consume, mopidy RPC url). Swap this per unit.
- **`src/walkman/mopidy_client.py`** — a tiny **stdlib-only** (urllib) HTTP JSON-RPC
  client with a `wait_until_ready` poll. Chose HTTP JSON-RPC over MPD because it's
  built into Mopidy core (no extra extension) and a short-timeout POST doubles as the
  "is Mopidy up yet?" reachability probe. Reused by the controller later.
- **`src/walkman/autoplay.py`** — waits for Mopidy + network, loads the playlist,
  sets shuffle/repeat/consume, plays. Has its own retry/backoff (`delays = [0, 5, 10,
  20, 30]`) to cover wifi and yt-dlp warm-up, and a generous 120 s RPC timeout
  because `tracklist.add` resolves tracks via ytmusicapi/yt-dlp.
- **`systemd/walkman-mopidy.service`** — Mopidy as `User=brew`, `After=network-
  online`, `Restart=always`.
- **`systemd/walkman-autoplay.service`** — oneshot, `Requires/After` mopidy +
  network.

### 5.2 Key decision — run as `brew`, not the `mopidy` user

Running Mopidy as `brew` reuses *everything proven in Step 1*: the config at
`~/.config/mopidy/mopidy.conf`, the auth at `~/.config/walkman/`, and audio-group
access. Lowest-risk choice for an appliance, and the per-device swap still works.
The stock `mopidy.service` is left **disabled** to avoid a port-6680 / ALSA-device
conflict.

### 5.3 Result + a minor open question

**✅ PASSED 2026-06-07.** Default volume lowered −6 dB (Speaker 39→35, Headphone
30→26) + `alsactl store`. One unresolved nit: autoplay's first track wasn't obviously
randomized across two boots (got the same track twice) — verify `set_random`
reshuffles the *start*. Minor.

---

## 6. Headphone auto-switch (2026-06-07)

**Goal:** for kids' evening use — speaker plays at low volume by default, but plug
headphones into the 3.5mm and the speaker mutes automatically.

**Why it's needed.** Recall from Step 0 (§3.5): the RT5645 machine driver powers the
headphone pin on jack-detect but does **not** mute the speaker. So without us, sound
comes out of *both* when headphones are inserted.

**What was built.** `src/walkman/jack_monitor.py` + `systemd/walkman-jack.service`
(runs as **root** — needs `/dev/input/event*` and the ALSA control):

- **Event-driven**, not polling: it blocks reading `struct input_event` records off
  the jack's `/dev/input/eventN`, watching for `EV_SW` / `SW_HEADPHONE_INSERT`.
- Finds the jack device **by name** (`/proc/bus/input/devices`, matching "headphone
  jack" + "voicebonnet") so it's **index-independent** — `event2` today might be
  something else after a kernel update.
- Sets the **initial** speaker state from `evtest --query` on startup (so it's
  correct even if you boot with headphones already in).
- Flips `Speaker Switch`: headphones IN → speaker OFF; OUT → speaker ON. Leaves the
  headphone path and volumes untouched. Stdlib only.

**Result.** Verified live both directions, including the empty-jack state reading
correctly (speaker on). Default volume lowered a **second** −6 dB on 2026-06-07:
Speaker 31 (0 dB), Headphone 22 (−13.5 dB), persisted.

---

## 7. Step 3 — Button gestures (2026-06-07)

**Goal:** map the one arcade button (GPIO 23) to player actions.

### 7.1 What was built

- `src/walkman/button.py`:
  - **`PressPattern`** — a **pure** state machine (no GPIO deps) so it's unit-tested
    without hardware (`tests/test_press_pattern.py` covers single / double / long /
    triple / spaced). Uses a `threading.Timer` over a `double_window` to disambiguate
    single vs double after release.
  - **`ButtonSource`** — wires `PressPattern` to a gpiozero `Button(23, pull_up=True,
    hold_time=1.2, bounce_time=0.05)`. `pull_up=True` because the AIY button pulls
    GPIO23 to ground when pressed (pressed = low). Long fires via gpiozero
    `when_held`; a `_held` flag suppresses the trailing release so a long press isn't
    also counted as a click.
- `src/walkman/led.py` — KTD2026 sysfs control (`set_rgb`/`off`; `shutdown_cue()` =
  solid white). Fails graceful on `OSError` — never crash the controller over the
  LED.
- `src/walkman/controller.py` — runs as **root** (GPIO + LED sysfs + poweroff), maps
  gestures → Mopidy actions over HTTP JSON-RPC.
- `config/walkman.toml` `[button]` section; `systemd/walkman-controller.service`.

**Gesture map:** single = play/pause toggle; double = next track; long (≥1.2 s) =
safe shutdown (white LED cue, then `systemctl poweroff`). Reshuffle was dropped (too
close to "next"). Safe shutdown mitigates SD-card corruption from a kid yanking
power. The controller folds button + LED + (future) Mopidy state polling into **one**
process to stay light on RAM.

### 7.2 The "next on a paused player stays paused → silence" bug

**What we hit.** Pressing "next" while paused advanced the track but stayed paused —
so "next" produced **silence**.

**Root cause.** `core.playback.next` on a paused player advances the tracklist
position but does not resume playback.

**Fix.** `next_track()` calls `core.playback.next` **then** `core.playback.play`. A
"next" press should always result in audible music. (See the comment in
`controller.py`.)

### 7.3 UX note

The arcade button has travel — a natural double-tap sometimes registered as two
singles. A firmer/quicker double-tap works; `double_click_window_seconds` (0.35) can
be widened in `walkman.toml` if needed.

**Status:** single + double user-confirmed by ear. Long-press shutdown is
**code-complete but UNTESTED** (it powers the Pi off).

---

## 8. The YouTube extraction reliability saga (the big one)

This is the most important section for a future maintainer. Chasing the "next" button
surfaced that **track switching was unreliable** — and the root cause is **YouTube's
anti-bot machinery (signature/nsig solving + PO tokens)**, not our code.

### 8.1 The chain of dependencies and the segfault

1. **No JS runtime → most tracks won't resolve** ("not playable"), so `next` can't
   advance. yt-dlp warns: "No supported JavaScript runtime… some formats may be
   missing."
2. **Installed `deno`** (`/usr/local/bin/deno`, arm64 release) — yt-dlp's JS runtime
   — **+ `yt-dlp[default]`**, which pulls in the **`yt-dlp-ejs`** challenge-solver
   scripts. This fixed JS **signature-solving**…
3. …but `yt-dlp[default]` **also pulled in `brotli` 1.2.0**, whose **C-extension
   SEGFAULTs inside Mopidy's worker threads on this ARM Pi.** Root-caused via a
   `faulthandler` thread backtrace pointing at **`_brotli` in
   `urllib3/response.py` `decompress`**. → **Removed brotli** (`pip uninstall
   brotli`); urllib3 falls back to gzip. Segfault gone, Mopidy stable again.

> If Mopidy starts crashing mysteriously after a yt-dlp/urllib3 upgrade, **check
> whether brotli got reinstalled.** This is a re-installation hazard every time you
> bump the yt-dlp stack.

### 8.2 The underlying wall — PO-token enforcement, and the client tradeoff

Even with JS-solving working, YouTube enforces **PO tokens**. The player-client
options on this weak Pi each have a cost:

- **`android_vr`**: fast, no JS-solving needed (returns unsigned URLs), but only
  ~1/6 tracks resolve (PO-token blocked).
- **`web` + deno**: resolves reliably, but ~20–30 s/track (JS solving on a weak CPU).
- **both (chosen)**: best coverage — fast android path first, slow web fallback.

### 8.3 Forcing the client without touching the package — the shim

We force the player client via **`shim/sitecustomize.py`**, loaded into the Mopidy
process by setting **`PYTHONPATH=/home/brew/walkman/shim`** in
`walkman-mopidy.service`. Python auto-imports `sitecustomize` at interpreter startup,
so the shim monkeypatches `yt_dlp.YoutubeDL.__init__` to set
`extractor_args.youtube.player_client = ["android_vr", "web"]`.

Why this approach:

- **Does NOT edit the Mopidy-YouTube package** (no dependency-source edits to lose on
  upgrade).
- **Reversible**: remove the PYTHONPATH entry (or the file) to undo.
- It uses `setdefault`, so an explicit caller param still wins, and it guards with a
  `_walkman_android_patch` flag so it only patches once.

### 8.4 Where it landed, and the robust-fix options

**Net result:** continuous playback works well in practice (user listened for an
hour; auto-advance is helped by prefetch). **Manual `next` occasionally stalls** on
an unresolvable track — just press again.

**If you want robust + fast `next` later:**

- **PO-token provider** — `bgutil-ytdlp-pot-provider`, a small node/deno sidecar.
  This is the lightweight version of "run a real browser" and is the recommended next
  step.
- **Headless browser** — would also work but is **impractical on a 512 MB Pi Zero 2
  W** (RAM/CPU/Widevine). It'd be fine on a Pi 4/5.

### 8.5 On-Pi changes to fold into `setup.sh`

- `deno` → `/usr/local/bin/deno` (arm64 release).
- `pip install yt-dlp[default]` (for yt-dlp-ejs) **then `pip uninstall brotli`** (the
  crasher).
- The shim + `PYTHONPATH` in `walkman-mopidy.service`.

Full detail: `docs/STEP3-NOTES.md`.

---

## 9. Service & deployment map (current reality)

Four systemd units, all enabled; the stock `mopidy.service` stays **disabled**.

| Unit | User | Type | Job |
|------|------|------|-----|
| `walkman-mopidy.service` | brew | simple | Mopidy + YouTube Music. `PYTHONPATH` shim. `Restart=always`. |
| `walkman-autoplay.service` | brew | oneshot | Load playlist, shuffle+repeat, play on boot. `RemainAfterExit=yes`, `TimeoutStartSec=300`. |
| `walkman-jack.service` | root | simple | Mute speaker when headphones inserted. |
| `walkman-controller.service` | root | simple | Button gestures → player actions; LED cues. |

- Code on the Pi at `/home/brew/walkman/{src,config,shim}`; units in
  `/etc/systemd/system/`.
- **All of the above deployment is currently manual** and needs to be folded into an
  idempotent `setup.sh` (AIY DKMS drivers + patches, deno, yt-dlp[default]-minus-
  brotli, the shim, the four units, the config.txt edit, the mixer baseline). See
  `docs/IDEAS.md` for the image-vs-setup.sh provisioning discussion.

---

## 10. Gotchas & hard-won lessons (READ THIS FIRST)

- **The stock kernel ships NO AIY drivers.** Not the codec, not the AIY IO MCU, not
  the LED. There's nothing to "unblock" — you must build the DKMS stack from
  `HorseyofCoursey/trixie-aiyprojects` @ `4ee62cedb2b0756e3503a11585a7064f7edd0871`,
  install **aiy first** (it provides the `aiy-gpio` supplier). The
  `googlevoicehat-soundcard` overlay is for the **v1 HAT** and does nothing here.

- **Two local patches are mandatory** or the build/install breaks:
  (1) the `leds-ktd202x` `.probe` single-arg signature for kernel 6.6+ (won't compile
  otherwise); (2) the `debian/control` headers-dependency rename
  (`raspberrypi-kernel-headers` → `linux-headers-rpi-v8 | …`) or **apt ends up in a
  broken state.**

- **The 3.5mm jack is silent until you enable `Headphone Channel Switch`.** The
  bonnet's UCM enables the *Speaker* channel switch but not the headphone one. The
  path will look fully powered (DAPM "HP amp On") and still be mute. Cost hours.

- **The machine driver does NOT auto-mute the speaker on headphone insert** — that's
  why `jack_monitor.py` exists.

- **LED color map: led1=red, led2=green, led3=blue** (led4 unused; KTD2026 = 3ch).
  **Raw I2C to `0x31` is blocked** once the kernel driver binds — only forced reads
  (`-f`) work, and you'd have to unload the driver to use it for real.

- **Durable OAuth is NOT supported** by Mopidy-YouTube 4.0.2 → we're on cookie/header
  auth, which **expires periodically.** `musicapi_cookiefile` is non-functional in
  4.0.2 — use `musicapi_browser_authentication_file`.

- **Chrome "Copy as cURL" redacts the cookie.** Use a `cookies.txt` export ("Get
  cookies.txt LOCALLY") + `scripts/ytmusic_auth_from_curl.py`.

- **ytmusicapi only treats a file as BROWSER auth if an `authorization: SAPISIDHASH…`
  header sits next to the cookie.** Missing it produces a misleading "oauth …
  oauth_credentials not provided" error. The converter injects it (the value's
  freshness doesn't matter — ytmusicapi recomputes per request).

- **`brotli`'s C-extension SEGFAULTs in Mopidy worker threads on ARM.** It gets
  pulled in by `yt-dlp[default]`. **Uninstall it** after installing the yt-dlp stack;
  re-check after every yt-dlp upgrade. Diagnose crashes with a `faulthandler` thread
  backtrace.

- **YouTube needs PO tokens / JS-solving.** Without deno, most tracks won't resolve.
  We force `player_client=[android_vr, web]` via a `sitecustomize` shim on
  `PYTHONPATH` (android = fast/limited, web = reliable/slow). Manual `next` can still
  stall on an unresolvable track — press again. Robust fix = a PO-token provider
  sidecar (`bgutil-ytdlp-pot-provider`); a headless browser is impractical on 512 MB.

- **`next` on a paused player stays paused → silence.** Always `next` *then* `play`.

- **Pin the YouTube stack as a unit** (`Mopidy-YouTube 4.0.2`, `ytmusicapi 1.12.1`,
  `yt-dlp 2026.3.17`) — it tracks unofficial APIs and breaks periodically. Upgrade
  together and re-test.

- **Find input devices by name, not index.** `event2` today may not be the headphone
  jack after a kernel update — `jack_monitor.py` resolves it via
  `/proc/bus/input/devices`.

- **Long-press shutdown is untested** (it powers the Pi off). Verify deliberately.

---

## 11. Open items / TODO

- Fold all on-Pi manual steps into an idempotent `setup.sh` (drivers + patches, deno,
  yt-dlp-minus-brotli, shim, four units, config.txt, mixer baseline).
- Step 4 LED status loop (green breathing while playing, amber paused, blue blink
  startup/wifi-down, red blink error, **magenta blink = needs re-auth**, white
  shutting-down). The LED module and controller are structured to grow into this.
- Verify long-press safe-shutdown for real.
- Verify `set_random` reshuffles the *first* track on boot.
- Power-loss resilience: implement the documented read-only-root / overlayfs
  procedure (kids yank power).
- Robust `next`: stand up `bgutil-ytdlp-pot-provider`.
- Family backlog (`docs/IDEAS.md`): repeat-track (Nathan, 12), now-playing OLED — both
  earmarked for the future CPX serial-satellite, since the one button is full.
- Kid-runnable re-auth ("cookie-monster") tied to the magenta LED state.

---

*Source notes this log synthesizes: `MEMORY.md` and the project memory files;
`docs/STEP0-NOTES.md`, `STEP1-PROGRESS.md`, `STEP2-NOTES.md`, `STEP3-NOTES.md`,
`PLAN.md`, `IDEAS.md`; `README.md`; `drivers/patches/*` + `drivers/prebuilt/README.md`;
`shim/sitecustomize.py`; `config/*`; `systemd/*`; and `src/walkman/*.py`.*
