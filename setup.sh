#!/usr/bin/env bash
#
# Walkman — idempotent system installer (account-agnostic).
#
# Reproduces everything that is IDENTICAL on every unit: AIY bonnet drivers, audio,
# Mopidy + YouTube Music stack, the controller/services, and the calm mixer baseline.
# It does NOT set the per-device account (cookies + playlist) — that's a separate,
# re-runnable step:  scripts/walkman-account.sh  (also the "cookie-monster" re-auth).
#
# Usage (on the Pi, with the repo cloned to /home/<user>/walkman):
#   cd ~/walkman && sudo ./setup.sh
#
# Safe to re-run: every step checks before acting.
#
# See docs/WORKLOG.md / docs/STEP*-NOTES.md for the why behind each step (esp. the
# brotli-segfault removal and the yt-dlp player-client shim).
set -euo pipefail

WALKMAN_USER="${WALKMAN_USER:-brew}"
USER_HOME="/home/${WALKMAN_USER}"
REPO="$(cd "$(dirname "$0")" && pwd)"
EXPECTED_DIR="${USER_HOME}/walkman"

log() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }
asuser() { sudo -u "$WALKMAN_USER" "$@"; }

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo: sudo ./setup.sh"; exit 1; }
id "$WALKMAN_USER" >/dev/null 2>&1 || { echo "User '$WALKMAN_USER' not found (set WALKMAN_USER=...)"; exit 1; }
if [ "$REPO" != "$EXPECTED_DIR" ]; then
  echo "WARNING: repo is at $REPO but the systemd units hardcode $EXPECTED_DIR."
  echo "         Clone/move the repo to $EXPECTED_DIR, or edit systemd/*.service paths."
fi

# 1. APT packages -------------------------------------------------------------
log "1/9 apt packages"
apt-get update -qq
apt-get install -y \
  build-essential dkms debhelper dh-dkms bc unzip device-tree-compiler curl \
  i2c-tools evtest alsa-utils network-manager \
  mopidy gstreamer1.0-alsa gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
  python3-pip python3-gpiozero python3-lgpio python3-smbus2 python3-serial
# Kernel headers for the DKMS driver builds (running kernel + the rpi meta).
apt-get install -y "linux-headers-$(uname -r)" 2>/dev/null \
  || apt-get install -y linux-headers-rpi-v8 || true

# 2. AIY bonnet DKMS drivers (RT5645 audio + KTD2026 LED + aiy-io MCU) ---------
# Prebuilt DKMS *source* debs (reviewed; rebuild against the running kernel).
log "2/9 AIY DKMS drivers"
# Filesystem check (robust; doesn't depend on `dkms` being on root's PATH).
if [ -d /var/lib/dkms/aiy ] && [ -d /var/lib/dkms/leds-ktd202x ] \
   && [ -d /var/lib/dkms/aiy-voicebonnet-soundcard ]; then
  echo "AIY DKMS modules already installed"
elif ls "$REPO"/drivers/prebuilt/aiy-dkms_*.deb >/dev/null 2>&1; then
  dpkg -i "$REPO"/drivers/prebuilt/aiy-dkms_*.deb \
          "$REPO"/drivers/prebuilt/aiy-voicebonnet-soundcard-dkms_*.deb \
          "$REPO"/drivers/prebuilt/leds-ktd202x-dkms_*.deb || true
  apt-get -y -f install || true   # satisfy deps if any
  echo "NOTE: if a future kernel breaks the fork's 6.12 patches, rebuild from source"
  echo "      (see docs/STEP0-NOTES.md / drivers/patches/) or hold the kernel."
else
  echo "ERROR: AIY DKMS not installed and no prebuilt debs in drivers/prebuilt/."
  echo "       Build from source (docs/STEP0-NOTES.md + drivers/patches/) first."
fi

# 3. Disable the SoC's built-in audio (the bonnet RT5645 is the card) ----------
log "3/9 config.txt: disable built-in audio"
CFG=/boot/firmware/config.txt
if grep -qE '^dtparam=audio=on' "$CFG"; then
  cp -n "$CFG" "${CFG}.bak-walkman" || true
  sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' "$CFG"
  echo "commented dtparam=audio=on (takes effect after reboot)"
else
  echo "built-in audio already disabled"
fi

# 4. CPX satellite audio tap --------------------------------------------------
log "4/9 CPX satellite audio tap"
install -m 0644 "$REPO/config/modules-load-walkman-satellite.conf" \
  /etc/modules-load.d/walkman-satellite.conf
if modprobe snd-aloop; then
  echo "snd-aloop loaded for the NeoPixel VU meter tap"
else
  echo "WARNING: could not load snd-aloop; CPX VU meter tap will not work yet"
fi
usermod -aG dialout,audio "$WALKMAN_USER" || true

# 5. deno — yt-dlp's JS runtime for YouTube signature solving ------------------
log "5/9 deno"
if [ -x /usr/local/bin/deno ]; then
  echo "deno present: $(/usr/local/bin/deno --version | head -1)"
else
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/deno.zip" \
    https://github.com/denoland/deno/releases/latest/download/deno-aarch64-unknown-linux-gnu.zip
  unzip -o -q "$tmp/deno.zip" -d "$tmp"
  install -m 0755 "$tmp/deno" /usr/local/bin/deno
  rm -rf "$tmp"
  echo "deno installed: $(/usr/local/bin/deno --version | head -1)"
fi

# 6. Python: Mopidy-YouTube + ytmusicapi + yt-dlp[default], then DROP brotli ----
# brotli's C-extension SEGFAULTs in Mopidy's worker threads on this ARM build
# (faulthandler pointed at _brotli in urllib3 decompress). Removing it = gzip fallback.
log "6/9 pip: Mopidy-YouTube / ytmusicapi / yt-dlp (then remove brotli)"
pip3 install --break-system-packages \
  "Mopidy-YouTube==4.0.2" "ytmusicapi==1.12.1" "yt-dlp[default]==2026.3.17"
pip3 uninstall -y --break-system-packages brotli >/dev/null 2>&1 || true
if python3 -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('brotli') is None else 1)"; then
  echo "brotli absent (good)"
else
  echo "WARNING: brotli still importable — Mopidy may segfault on extraction"
fi

# 6b. Patch Mopidy-YouTube: one unguarded result["author"] KeyErrors and kills the
# ENTIRE playlist load when ytmusicapi omits "author" for a playlist (hit with a kid's
# playlist that contained a plain YouTube video, not a "song"). Per-track parsing
# already tolerates a missing author; only this playlist-level line didn't. The patch
# logic lives in scripts/patch_mopidy_youtube.py (idempotent; regression-tested).
python3 "$REPO/scripts/patch_mopidy_youtube.py"

# 7. Mopidy config + per-device dirs ------------------------------------------
log "7/9 mopidy.conf + config dirs"
asuser mkdir -p "$USER_HOME/.config/mopidy" "$USER_HOME/.config/walkman"
if [ -f "$USER_HOME/.config/mopidy/mopidy.conf" ]; then
  echo "mopidy.conf already present (left as-is)"
else
  asuser cp "$REPO/config/mopidy.conf.example" "$USER_HOME/.config/mopidy/mopidy.conf"
  echo "installed mopidy.conf from example"
fi

# 8. systemd units ------------------------------------------------------------
log "8/9 systemd services + log retention"
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 "$REPO/config/journald-walkman.conf" \
  /etc/systemd/journald.conf.d/walkman.conf
install -m 0644 "$REPO"/systemd/walkman-*.service /etc/systemd/system/
install -m 0644 "$REPO/config/99-walkman-cpx.rules" /etc/udev/rules.d/
udevadm control --reload-rules || true
udevadm trigger || true
systemctl daemon-reload
systemctl restart systemd-journald.service || true
systemctl disable mopidy.service >/dev/null 2>&1 || true   # avoid port 6680 clash
systemctl enable walkman-satellite.service walkman-mopidy.service walkman-autoplay.service \
                 walkman-controller.service walkman-jack.service
echo "enabled: walkman-satellite, walkman-mopidy, walkman-autoplay, walkman-controller, walkman-jack"
echo "journald capped by /etc/systemd/journald.conf.d/walkman.conf"

# 9. ALSA baseline (RT5645 routing + channel switches + calm volumes) ----------
log "9/9 ALSA mixer baseline"
install -m 0644 "$REPO/config/asound.state" /var/lib/alsa/asound.state
alsactl restore 2>/dev/null || alsactl restore aiyvoicebonnet 2>/dev/null || true
echo "restored mixer state"

cat <<EOF

Walkman system install complete.
Next:
  1) sudo reboot          # for the audio config + drivers to take effect
  2) Set this unit's account (cookies + playlist):
       ./scripts/walkman-account.sh --cookies <cookies.txt> --playlist <PLAYLIST_ID>
After that it boots straight into music. See README.md for the full runbook.
EOF
