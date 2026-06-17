# WORKLOG.md — critical review (from a stuck dev at 1am)

> **Historical review, mostly addressed.** This file is kept because it explains what
> makes a good troubleshooting runbook. Many recommendations below have already been
> folded into `WORKLOG.md`; use it as critique/history, not as the current bug list.

> Audience: someone with a Pi Zero 2 W + AIY Voice Bonnet trying to (a) reproduce this
> build and (b) debug their own variant, **with WORKLOG.md as their only guide.** This
> is a critique, not a victory lap. The worklog is genuinely good *narrative* — the
> "Gotchas" section (§10) and the honest dead-ends are the best part — but as a
> **reproduction document it is not runnable**, and several confident claims are
> actually best-guesses. Below, prioritized by how badly a stuck dev gets burned.
>
> Line/section refs are to WORKLOG.md as of 2026-06-07.

---

## HIGH VALUE TO ADD

### H1. The worklog has almost no actual commands. You cannot reproduce a single step from it alone.
This is the #1 problem. The worklog *describes* every step but rarely *shows the
command*. A stuck dev can't copy-paste anything. Concretely missing:

- **§3.1 (driver build)** names the fork + commit and says "built with
  `dpkg-buildpackage -b -us -uc` and installed aiy first" — but never shows the clone,
  checkout, patch-apply, per-package build loop, or the `dpkg -i` order. The
  *runnable* version exists in `drivers/prebuilt/README.md` (clone-free install path)
  but **a worklog-only reader never sees it.** Add, near §3.1:
  ```bash
  # build deps
  sudo apt-get install -y dkms debhelper dh-dkms build-essential bc evtest linux-headers-rpi-v8
  # source
  git clone https://github.com/HorseyofCoursey/trixie-aiyprojects ~/walkman-build/aiy-src
  cd ~/walkman-build/aiy-src && git checkout 4ee62cedb2b0756e3503a11585a7064f7edd0871
  # apply the two patches (see §3.2) BEFORE building, then per package:
  cd <pkgdir> && dpkg-buildpackage -b -us -uc
  # install order matters — aiy first (provides aiy-gpio supplier):
  sudo dpkg -i aiy-dkms_*.deb aiy-voicebonnet-soundcard-dkms_*.deb leds-ktd202x-dkms_*.deb
  sudo apt-get -f install -y
  ```
  Or, point explicitly at the fast path: "To skip the build, install the checkpoint
  debs per `drivers/prebuilt/README.md`." **The worklog never mentions that the
  prebuilt debs + their install recipe exist** — flag that omission loudly.

- **§3.2 patch #2 (control deps)** quotes the *fixed* Depends line but not the `sed`
  that applies it. The exact one-liner is in `drivers/patches/control-deps.md` and
  should be inlined:
  ```bash
  sed -i "s@raspberrypi-kernel-headers, dkms@linux-headers-rpi-v8 | linux-headers-rpi-2712 | raspberrypi-kernel-headers, dkms@" debian/control
  ```
  Also: patch #1's actual diff lives in `drivers/patches/leds-ktd202x-probe-6.12.patch`
  — reference the file path so the reader knows where the patch text is.

- **§4.1 (Mopidy install)** never shows the install commands. Add:
  ```bash
  sudo apt-get install -y mopidy gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
  pip install --break-system-packages 'Mopidy-YouTube==4.0.2' 'ytmusicapi==1.12.1' 'yt-dlp==2026.3.17'
  ```
  (Versions are in §4.1 prose; the *commands* aren't.)

- **§8 (deno + yt-dlp[default] + brotli)** — the single most fragile area — has **zero
  commands.** "Installed `deno` (arm64 release)" with no URL or method;
  "`yt-dlp[default]`" with no pip line; "`pip uninstall brotli`" is the only literal
  command in the whole section. Add:
  ```bash
  curl -fsSL https://deno.land/install.sh | sh    # or download the aarch64 release tarball to /usr/local/bin/deno
  deno --version                                   # verify it runs on armv8
  pip install --break-system-packages 'yt-dlp[default]'
  pip uninstall -y brotli brotlicffi             # the crasher; re-check after every yt-dlp bump
  deno --version && yt-dlp --version
  ```
  Note `brotlicffi` too — `[default]` can pull either; the worklog only names `brotli`.

- **Service enablement (§9)** says "all enabled" but never shows how. Add the literal:
  ```bash
  sudo systemctl disable --now mopidy            # kill the stock unit (port 6680 / ALSA conflict)
  sudo cp systemd/walkman-*.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now walkman-mopidy walkman-autoplay walkman-jack walkman-controller
  ```

- **The shim wiring (§8.3)** describes `PYTHONPATH=/home/brew/walkman/shim` but doesn't
  show *where* it goes. State plainly: it's an `Environment=` line in
  `systemd/walkman-mopidy.service` (line 14 of that file) and won't take effect for a
  manually-launched `mopidy` unless you export it. A stuck dev testing Mopidy by hand
  will be baffled why the shim "doesn't load."

- **§4.2 re-auth** says "export cookies.txt → run the converter → scp → restart" but
  the actual converter invocation lives only in `STEP1-PROGRESS.md`. Inline it:
  ```bash
  python3 scripts/ytmusic_auth_from_curl.py -o /tmp/ytmusic-auth.json < ~/Downloads/music.youtube.com_cookies.txt
  scp /tmp/ytmusic-auth.json brew@walkman-a.local:/home/brew/.config/walkman/ytmusic-auth.json
  rm -f /tmp/ytmusic-auth.json
  sudo systemctl restart walkman-mopidy
  ```
  Also note the file must be **mode 600** and the directory `~/.config/walkman/` must
  exist.

**Where:** add a runnable command block under each of §3.1, §3.2, §4.1, §8.5, §9, plus
a worklog-top pointer to `drivers/prebuilt/README.md` and `setup.sh` (when it exists).

### H2. Diagnostic *technique* is named but not taught — a stuck dev can't reproduce the methods.
The worklog states conclusions ("root-caused via a faulthandler thread backtrace")
but not the *how*, so a dev hitting a **different** failure can't apply the method.
Add a short "How we found it" line/command to each war story:

- **brotli segfault (§8.1):** show how to get the backtrace and read the crash
  signature. Add:
  ```bash
  journalctl -u walkman-mopidy -b | grep -E 'code=killed|status=11|SIGSEGV|signal'
  # to capture a Python-thread backtrace at crash time:
  PYTHONFAULTHANDLER=1 mopidy --config ~/.config/mopidy/mopidy.conf
  # or in code: faulthandler.enable(); faulthandler.dump_traceback_later(...)
  ```
  Explain the signature: systemd reports `code=killed, status=11/SEGV` for a native
  crash; that's the tell that it's a C-extension, not Python. The worklog asserts the
  finding but never shows the journalctl line that *is* the symptom.

- **The "no driver to bind" trap (§3.1):** the worklog *mentions* `waiting_for_supplier`
  read `0` and dmesg showed no deferred probe — good — but doesn't show the commands.
  Add:
  ```bash
  aplay -l                                  # only vc4hdmi == no bonnet card
  ls /sys/class/leds                        # no ktd entry
  dmesg | grep -iE 'deferred|supplier|rt5645|aiy|ktd'
  find /proc/device-tree -name 'waiting_for_supplier' -exec cat {} \;
  ```

- **Silent jack (§3.4):** the diagnostic chain (DAPM showed "HP amp On" yet silence)
  is the lesson, but the commands to *see* DAPM/mixer state are missing. Add:
  ```bash
  aplay -l; aplay -L
  amixer -c aiyvoicebonnet contents         # dump ALL controls incl. *Channel Switch
  amixer -c aiyvoicebonnet scontrols
  cat /sys/kernel/debug/asoc/*/dapm/*        # (debugfs) which widgets are powered
  ```

- **Jack detection (§3.5):** the `evtest --query` line *is* shown (good) — keep it, and
  add `cat /proc/bus/input/devices` (how `jack_monitor.py` finds the device by name).

**Where:** either inline per-section, or — better — a new "§Diagnostics toolbox"
subsection collecting `journalctl` crash-signature reading, `aplay -l/-L`,
`amixer contents`, `evtest --query`, `dmesg | grep`, `/proc/device-tree`
deferred-probe checks, and `PYTHONFAULTHANDLER=1`.

### H3. No external links anywhere. A stuck dev wants URLs and there are none.
The worklog names a dozen external resources by *string* and links *zero* of them. Add
a "References / external links" section (and inline the key ones):

- Fork repo + pinned commit (§3.1):
  `https://github.com/HorseyofCoursey/trixie-aiyprojects` @
  `4ee62cedb2b0756e3503a11585a7064f7edd0871`. Note the lineage google → viraniac →
  HorseyofCoursey and link the upstreams if known.
- **The Hackster article / Walkman build this reproduces** — referenced as "this
  Walkman build" in the task framing but **not linked anywhere in the repo.** If a
  source article exists, link it; if not, say "no upstream article — this is original."
- yt-dlp EJS / JS-runtime wiki:
  `https://github.com/yt-dlp/yt-dlp/wiki/EJS` and the JS-runtime note.
- yt-dlp PO-Token guide: `https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide`.
- PO-token provider sidecar (§8.4):
  `https://github.com/Brainicism/bgutil-ytdlp-pot-provider`.
- ytmusicapi docs (browser-auth / setup):
  `https://ytmusicapi.readthedocs.io/` (and the auth-setup page that explains
  SAPISIDHASH / browser auth).
- Mopidy-YouTube (natumbri): `https://github.com/natumbri/mopidy-youtube`.
- Archived Mopidy-YTMusic (so the reader sees *why* it was rejected):
  `https://github.com/OzymandiasTheGreat/mopidy-ytmusic`.
- "Get cookies.txt LOCALLY" extension (§4.2) — link the Chrome Web Store / repo so a
  dev installs the *right* one (there are malicious clones).
- RT5645 / KTD2026 / AIY: link the mainline `rt5645` kernel driver doc, the KTD2026
  datasheet, and the AIY Voice Bonnet pinout / GPIO map (GPIO23 button claim in §7
  depends on this).

**Where:** new "§References" at the end, plus inline the fork URL at §3.1 and the
PO-token URLs at §8.4.

### H4. Several confident claims are actually guesses — say so.
The worklog's tone is authoritative even where the team was clearly working around
something it didn't fully understand. Add explicit humility markers:

- **brotli root cause (§8.1):** "whose C-extension SEGFAULTs inside Mopidy's worker
  threads on this ARM Pi." This is a **correlation + a backtrace pointing at `_brotli`**
  — not a proven root cause (no upstream bug filed/linked, no minimal repro shown).
  Reword to: "*We believe* brotli's C-extension is the crasher (faulthandler pointed at
  `_brotli` in urllib3's `decompress`); removing it eliminated the segfault. We did not
  isolate a minimal repro or confirm upstream. **This is a workaround, not a root
  fix.**"
- **`android_vr` vs `ios` vs `web` (§8.2):** stated as settled, but the **shim
  docstring itself mentions `ios`** as an equivalent unsigned-URL client — i.e., the
  choice between `android_vr` and `ios` was not rigorously compared. Say: "we picked
  `android_vr` empirically; `ios` is a near-equivalent unsigned-URL client we did not
  benchmark." Also note the "~1/6 tracks" and "~20–30 s/track" figures are
  **anecdotal single-session measurements**, not benchmarks.
- **SAPISIDHASH classification (§4.2):** the explanation of `is_browser` /
  `determine_auth_type` is presented as fact. It's a *reverse-engineered reading* of
  ytmusicapi 1.12 internals (and cites a line number `ytmusic.py:180` only in
  STEP1-PROGRESS, not the worklog). Mark it "our reading of ytmusicapi 1.12 source —
  may differ in other versions" and **carry the `ytmusic.py:180` reference into the
  worklog.**
- **"fail-open wifi check":** the autoplay/network-wait behavior is described as having
  retry/backoff (§5.1) but the worklog never states whether the wifi/Mopidy check
  *fails open or closed* on persistent failure. The task asks about a "fail-open wifi
  check" — if that's the design, the worklog doesn't say it. Clarify the actual
  behavior and whether it was deliberate.
- **The whole player-client choice (§8.2):** flag that this is **not durable** — see
  H6 (rot). Currently reads as a permanent decision.
- **Segfault "understood":** §8.1 reads as if fully understood; pair it with the
  workaround caveat above.

**Where:** inline edits at §8.1, §8.2, §4.2, plus one sentence in §5.1.

---

## MEDIUM

### M1. Add a symptom → cause → fix troubleshooting table.
§10 is a great prose checklist but a stuck dev scans a table faster. Add near §10:

| Symptom | Likely cause | Fix / check |
|---|---|---|
| `aplay -l` shows only `vc4hdmi`, no `aiyvoicebonnet` | AIY DKMS drivers not installed/built | Build/install the DKMS stack (§3.1); `dmesg \| grep -i aiy`; verify `aiy` installed first |
| apt in broken state after installing driver debs | stale `raspberrypi-kernel-headers` dep | apply control-deps sed (§3.2); `sudo apt-get -f install` |
| `leds-ktd202x` won't compile | missing `.probe` signature patch | apply `leds-ktd202x-probe-6.12.patch` (§3.2) |
| Card up, speaker works, **3.5mm jack silent** | `Headphone Channel Switch` off (UCM omission) | `amixer ... set 'Headphone Channel Switch' on`; `alsactl store` (§3.4) |
| Sound from **both** speaker + headphones when plugged in | machine driver doesn't auto-mute | ensure `walkman-jack.service` running (§6) |
| `/sys/class/leds/...` returns "Device or resource busy" on i2c | kernel driver holds 0x31 | use sysfs `brightness`, not raw i2c; unload `leds_ktd202x` only if you must (§3.6) |
| Mopidy: "oauth JSON provided … oauth_credentials not provided" | auth file missing `authorization: SAPISIDHASH…` header | regenerate via `ytmusic_auth_from_curl.py` (§4.2) |
| Mopidy dies with `code=killed status=11/SEGV` | brotli C-ext reinstalled by yt-dlp bump | `pip uninstall brotli brotlicffi` (§8.1) |
| `next` advances but plays silence | `next` on paused player stays paused | controller does `next` then `play` (§7.2) |
| Most tracks "not playable" / `next` stalls | no JS runtime / PO-token wall | install deno; confirm shim forces `[android_vr, web]` (§8) |
| yt-dlp warns "No supported JavaScript runtime" | deno missing or not on PATH | install deno to `/usr/local/bin`; `deno --version` |
| LED state never updates | controller not running as root | `walkman-controller.service` runs as root (GPIO+sysfs) |

### M2. Surface environment assumptions a reproducer needs *before* step 0.
These are scattered in PLAN/STEP0 but a worklog-only reader hits them as surprises. Add
a "§0 Prerequisites / environment" block stating:
- Exact image: **Raspberry Pi OS Lite 64-bit (Bookworm)**, kernel
  `6.12.87+rpt-rpi-v8`. (Worklog mentions kernel but not "you must start from Lite
  64-bit Bookworm.")
- The **Mac↔Pi bridge**: authored on Mac at `/Users/brew/Code/walkman`, deployed over
  passwordless SSH (ed25519) to `brew@walkman-a.local`, persistent tmux shell on the
  Pi. (In §1 but worth making a numbered prereq.)
- **File-location map (repo vs Pi)** — this is genuinely confusing and only partially
  stated:
  - repo `src/`,`config/`,`shim/` → Pi `/home/brew/walkman/{src,config,shim}`
  - repo `systemd/*.service` → Pi `/etc/systemd/system/`
  - auth → Pi `~/.config/walkman/ytmusic-auth.json` (mode 600)
  - mopidy config → Pi `~/.config/mopidy/mopidy.conf`
  - converter also copied to Pi at `/home/brew/ytmusic_auth_from_curl.py`
  - source clone → Pi `~/walkman-build/aiy-src`
  - config.txt backup → `/boot/firmware/config.txt.bak-walkman`
- **Mopidy runs as `brew`, group `audio`**, with `HOME=/home/brew` and `PYTHONPATH`
  set — and the stock `mopidy` user is unused. (In §5.2 but a reproducer needs it up
  front because every path is under `/home/brew`.)
- The **`.ssh/config` `ServerAliveInterval 30`** tip (buried in §4 blockquote) — pull
  it into prereqs; dropped sessions during the long driver build are painful.

### M3. The mopidy.conf is never shown in the worklog.
A reproducer needs the actual `[youtube]` + `[audio]` config. It exists at
`config/mopidy.conf.example` but the worklog doesn't reference it. Add a pointer and at
least show the load-bearing lines:
```ini
[audio]
output = alsasink device=plughw:CARD=aiyvoicebonnet
[youtube]
musicapi_enabled = true
musicapi_browser_authentication_file = /home/brew/.config/walkman/ytmusic-auth.json
youtube_dl_package = yt_dlp
```
Note `musicapi_cookiefile` is non-functional (worklog says this in §4.2 but doesn't
show the working config).

### M4. The exact mixer commands to reach the §3.4 baseline are missing.
§3.4 lists the target values but not how to set them. Add:
```bash
amixer -c aiyvoicebonnet set 'Headphone Channel Switch' on
amixer -c aiyvoicebonnet set 'Speaker Channel Switch' on
amixer -c aiyvoicebonnet set 'Headphone' 30
amixer -c aiyvoicebonnet set 'Speaker' 35
sudo alsactl store
```
(Control names per the bonnet UCM; reader should `amixer contents` to confirm exact
names.)

---

## NICE-TO-HAVE

### N1. Date-stamp the rot warning and add a "this will break — re-diagnose like this".
§8 mentions versions drift but doesn't *date* the fragility or give a re-diagnosis
recipe. Add a dated callout:
> **As of 2026-06-07**, YouTube extraction relies on deno + `yt-dlp 2026.3.17` +
> `player_client=[android_vr, web]`. **This approach has a shelf life of weeks-to-
> months.** When playback breaks: (1) `yt-dlp --verbose <url>` on the Pi to see the
> current extractor error; (2) check the yt-dlp changelog + PO-Token wiki for the new
> required client; (3) update `PLAYER_CLIENTS` in `shim/sitecustomize.py`; (4) re-check
> brotli isn't back; (5) consider the PO-token sidecar. The specific client names,
> commit hashes, and the whole anti-bot strategy *will* change.

Also add dates to the pinned-version block (§4.1) and the fork commit date
(2026-04-05, already noted — keep it).

### N2. Reconcile the shim/worklog `ios` discrepancy.
WORKLOG §8.3 / §8.5 and the gotcha (§10) say `[android_vr, web]`. The **shim
docstring** (`shim/sitecustomize.py`) discusses `ios` as an equivalent. Either align
the prose or add a sentence: "`ios` is a documented alternative to `android_vr` (both
return unsigned URLs); we standardized on `android_vr`."

### N3. State the swap/RAM build prerequisite.
STEP0-NOTES mentions "511 MB swap" was needed for the DKMS build on 512 MB RAM. The
worklog (§1) harps on RAM constraints but never warns that **the driver compile can
OOM without swap.** Add: "ensure ~512 MB swap before `dpkg-buildpackage` — the kernel
module compile is the heaviest step on a 512 MB Pi."

### N4. Note the `set_random` open question is still open, and how to test it.
§5.3 flags the un-reshuffled first track as "minor." Add the concrete check: call
`core.tracklist.set_random(true)` *after* loading and confirm `get_random()` and that
`index 0` differs across boots — and note the workaround (explicit shuffle before
play). Currently it's a dangling nit with no repro.

### N5. Long-press shutdown is untested — make that scarier / give a safe test.
§7.1/§10 note it's untested. Add: test with `journalctl -f` + a manual long-press and
watch for the `poweroff` log line, or temporarily swap `systemctl poweroff` for
`echo POWEROFF` in `controller.py` to validate the gesture path without losing the box.

### N6. Mention `tests/` exist.
The worklog references `tests/test_press_pattern.py` (§7.1) but never tells a reader to
run them: `python3 -m pytest tests/` (these are the only hardware-free checks in the
repo — valuable for a dev who can't touch the Pi).

---

## What the worklog already does WELL (so a rewrite doesn't lose it)
- The §10 "Gotchas" checklist is excellent and correctly front-loaded.
- Honest dead-ends (OAuth not supported, Chrome cURL redaction, the v1-HAT overlay red
  herring) save real hours.
- The §2 "plan vs reality" reconciliation is exactly right — keep it.
- Pinning versions "as a unit" and the §9 service table are good operational hygiene.

The core deficiency is simple: **it reads like a story, not a runbook.** Everything in
"HIGH" is about turning narrated actions into copy-pasteable commands + linked sources,
and tagging the guesses as guesses.
