#!/usr/bin/env bash
#
# Walkman — per-device account setup. ONE script, two uses:
#   * Provisioning a new unit:  --cookies + --playlist + --hostname
#   * Re-auth ("cookie-monster") when the LED goes magenta / cookies expire:  --cookies
#
# It turns a YouTube Music cookies.txt (or a DevTools "Copy as cURL") into this
# device's auth file, optionally sets the playlist + hostname, then restarts playback.
# Same code path for both — a fresh install and a re-auth differ only in which flags
# you pass.
#
# Run ON THE PI as the walkman user (it uses sudo for service restarts):
#   ~/walkman/scripts/walkman-account.sh --cookies cookies.txt [--playlist ID] [--hostname NAME]
#
# Getting cookies.txt: on a logged-in computer, export music.youtube.com cookies with
# the open-source "Get cookies.txt LOCALLY" browser extension, then copy the file to
# the Pi (scp / USB). See README "Re-auth (cookie-monster)" for the one-liner.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
AUTH_OUT="$HOME/.config/walkman/ytmusic-auth.json"
CONVERTER="$REPO/scripts/ytmusic_auth_from_curl.py"
TOML="$REPO/config/walkman.toml"

COOKIES=""; PLAYLIST=""; HOSTNAME_NEW=""
while [ $# -gt 0 ]; do
  case "$1" in
    --cookies)  COOKIES="$2";      shift 2;;
    --playlist) PLAYLIST="$2";     shift 2;;
    --hostname) HOSTNAME_NEW="$2"; shift 2;;
    -h|--help)
      echo "usage: $0 --cookies <file> [--playlist <ID>] [--hostname <NAME>]"; exit 0;;
    *) echo "unknown arg: $1"; exit 1;;
  esac
done
[ -n "$COOKIES" ] || { echo "error: --cookies <cookies.txt | curl.txt> is required"; exit 1; }
[ -f "$COOKIES" ] || { echo "error: cookies file not found: $COOKIES"; exit 1; }

# Always shred a cookie we were handed in /tmp, on ANY exit — don't depend on the
# caller's "&& rm" (which gets skipped if anything below exits non-zero, as it did when
# a kid's playlist failed to load and left the cookie sitting on disk). Never touch a
# cookies.txt the user keeps in their repo/home.
cleanup_tmp_cookie() {
  case "$COOKIES" in
    /tmp/*) rm -f "$COOKIES" 2>/dev/null && echo "==> removed $COOKIES (cookie no longer needed on disk)";;
  esac
}
trap cleanup_tmp_cookie EXIT

# 1. cookies/cURL -> ytmusicapi browser-auth JSON (reuses the converter; injects the
#    SAPISIDHASH 'authorization' header so ytmusicapi classifies it as BROWSER auth).
echo "==> writing auth file"
mkdir -p "$(dirname "$AUTH_OUT")"
python3 "$CONVERTER" -o "$AUTH_OUT" < "$COOKIES"
chmod 600 "$AUTH_OUT"

# 2. optional: set this device's playlist
if [ -n "$PLAYLIST" ]; then
  sed -i -E "s|^id = \".*\"|id = \"$PLAYLIST\"|" "$TOML"
  echo "==> playlist set: $PLAYLIST"
fi

# 3. optional: set hostname (provisioning a new unit)
if [ -n "$HOSTNAME_NEW" ]; then
  sudo hostnamectl set-hostname "$HOSTNAME_NEW"
  echo "==> hostname set: $HOSTNAME_NEW (full effect after reboot)"
fi

# 4. restart playback with the new credentials/playlist.
# autoplay is a oneshot that fails (non-zero) if it can't START playback — e.g. an
# empty/private playlist. That's NOT an auth failure, so don't crash the script with a
# scary systemd error: catch it and explain. (systemd's own stderr is hidden so the
# kid sees our friendly message, not "Job for walkman-autoplay.service failed".)
echo "==> restarting Mopidy + autoplay"
sudo systemctl restart walkman-mopidy.service
if sudo systemctl restart walkman-autoplay.service 2>/dev/null; then
  echo "✅ Done — your music should be playing now (LED breathing green)."
else
  echo
  echo "⚠️  Your login was saved and the playlist was set, but the music didn't start."
  echo "    Almost always this is the PLAYLIST, not your login — it may be empty, set to"
  echo "    private, or contain a video that isn't a song. Try a different playlist, or"
  echo "    see what happened with:"
  echo "        journalctl -u walkman-autoplay -n 20 --no-pager"
  echo "    (The box will also try again each time you power it on.)"
fi
[ -n "$HOSTNAME_NEW" ] && echo "(You changed the hostname — reboot when convenient.)"
exit 0
