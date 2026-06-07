# Walkman — Step 1 notes

**Status: Step 1 (Mopidy + YouTube Music) — ✅ PASSED 2026-06-06.**
Proven end-to-end on hardware: authenticated YouTube Music (user's subscription) →
playlist (24 tracks loaded) → yt-dlp → GStreamer → AIY bonnet, audible. Verified
`state=playing`, real track ("More To This" – Marc Scibilia), position advancing,
user confirmed audio. Shuffle+repeat on, consume off.

## Done so far

- **Mopidy core 3.4.1 + GStreamer 1.22** installed via apt (`mopidy gstreamer1.0-plugins-good/bad/ugly`).
- **YouTube Music extension chosen: `Mopidy-YouTube` (natumbri)** — the dedicated
  `Mopidy-YTMusic` is archived/stale; natumbri's is actively maintained and uses
  `yt-dlp`. Installed via pip (`--break-system-packages`). **Pinned/working versions:**
  - `Mopidy-YouTube == 4.0.2`
  - `ytmusicapi == 1.12.1`
  - `yt-dlp == 2026.3.17`
  (These track an unofficial API and break periodically — upgrade them as a unit and re-test.)
- **Test config written on the Pi:** `~/.config/mopidy/mopidy.conf` (see
  `config/mopidy.conf.example`). Audio → bonnet (`alsasink device=plughw:CARD=aiyvoicebonnet`),
  HTTP JSON-RPC on `127.0.0.1:6680`, `[youtube]` with `musicapi_enabled = true`,
  `youtube_dl_package = yt_dlp`, auth file path set.
- **Mopidy verified to start** as user `brew`: loads the YouTube backend, logs
  *"Using YouTube Music API"*, then exits only because the auth file is missing.
  Pipeline (extensions, HTTP frontend, GStreamer) is otherwise healthy.

## Auth findings (the fragile part the user flagged)

- Durable `client_id/secret` OAuth is **not** supported by Mopidy-YouTube 4.0.2:
  it calls `YTMusic(auth=file)` with no `oauth_credentials`. So we're on **browser/
  header (cookie) auth** (accepted fallback; re-auth procedure + LED indicator planned).
- The only working auth input in 4.0.2 is **`musicapi_browser_authentication_file`**
  — a JSON dict of request headers (incl. the auth cookie). `musicapi_cookiefile`
  is **commented out / non-functional** in this version.
- **Re-auth tool written:** `scripts/ytmusic_auth_from_curl.py` (also on the Pi at
  `/home/brew/ytmusic_auth_from_curl.py`). Converts a Chrome "Copy as cURL" **or** a
  Netscape `cookies.txt` into the auth JSON. Auto-detects input, prints only safe
  diagnostics (header names / cookie length), never secrets. Writes to
  `~/.config/walkman/ytmusic-auth.json` (mode 600).

## How auth was solved (and the gotcha)

- Chrome "Copy as cURL" **redacts the cookie** here — dead end. Used the
  **cookies.txt** route ("Get cookies.txt LOCALLY" extension → export → converter).
- **Gotcha that cost the most time:** ytmusicapi 1.12 classifies an auth file as
  BROWSER only if it has an **`authorization` header containing `SAPISIDHASH`** next
  to the `cookie` (`is_browser` needs both; `determine_auth_type` keys off
  `SAPISIDHASH`). Without it, it assumes OAuth and errors ("oauth JSON provided …
  oauth_credentials not provided"). ytmusicapi *recomputes* that hash per request
  (`ytmusic.py:180`), so the file just needs one present at generation time.
- `scripts/ytmusic_auth_from_curl.py` now computes & injects that `authorization`
  header (+ origin/x-origin). So the documented re-auth procedure is simply:

### Re-auth procedure (when cookie auth expires)
1. `music.youtube.com` logged into the account → "Get cookies.txt LOCALLY" → Export.
2. On the Mac:
   ```bash
   python3 /Users/brew/Code/walkman/scripts/ytmusic_auth_from_curl.py \
       -o /tmp/ytmusic-auth.json < ~/Downloads/music.youtube.com_cookies.txt \
     && scp /tmp/ytmusic-auth.json brew@walkman-a.local:/home/brew/.config/walkman/ytmusic-auth.json \
     && rm -f /tmp/ytmusic-auth.json && echo DEPLOYED
   ```
3. Restart Mopidy. (Future: the controller surfaces "needs re-auth" as a magenta LED blink.)

- A loose `copied_as_curl.txt` (had a cookie) was found in the repo dir,
  git-excluded, and deleted; auth-secret patterns are in `.gitignore`.

## Carry-forward into Step 2+

- A **foreground test Mopidy is currently running as `brew`** (PID in `~/mopidy.pid`,
  log `~/mopidy.log`). **Stop it before Step 2** (`kill $(cat ~/mopidy.pid)`) so it
  doesn't clash with the systemd service (port 6680 / ALSA device).
- Move Mopidy to a **systemd service** (mopidy user) + relocate auth to a per-device
  system path; sort file permissions for the service user.
- Auto-play on boot, button gestures, LED states (incl. magenta re-auth blink),
  speaker↔headphone auto-switch.

## Then, to finish Step 1 (the gate)

1. Confirm auth file present on Pi: `ls -l ~/.config/walkman/ytmusic-auth.json`.
2. Start Mopidy as brew: `mopidy` (foreground) — watch it log YouTube Music auth OK.
3. Via HTTP JSON-RPC (port 6680), load the playlist, enable random+repeat, play:
   - Playlist ID: **`PL5bKS0Bw-MfRrtcFhf0SQeo-evkVM8Wgx`** (URI for the ext: `yt:https://music.youtube.com/playlist?list=PL5bKS0Bw-MfRrtcFhf0SQeo-evkVM8Wgx`)
4. **Gate (must pass):** a **full track streams from the user's subscription** (not
   previews/search-only), audible through the bonnet. Test account first.

## Carry-forward / reminders

- Mopidy is being run as `brew` for testing; **Step 2 moves it to a systemd service**
  (mopidy user) with a system auth path (e.g. `/etc/walkman/`), and sorts file perms.
- **Per-device auth**: the auth-json path is referenced from config (not hardcoded) —
  per the design, provisioning unit #2 = swap the auth file + playlist ID + hostname.
- **LED re-auth indicator** (slow magenta blink) added to the plan for when cookie
  auth expires.
- Speaker↔headphone **auto-switch** (mute speaker on jack insert) still to implement
  in the controller (jack at input `event2`).
- Minor: the user's own SSH session to the Pi keeps dropping ("closed by remote
  host"); large terminal pastes truncate — prefer the clipboard/file methods above.
  Consider adding `ServerAliveInterval 30` to their `~/.ssh/config`.
