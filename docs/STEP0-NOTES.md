# Walkman — Step 0 dev notes / checkpoint

**Status: Step 0 (audio + LED foundations) COMPLETE and verified on real hardware, incl. across reboot.**
Date: 2026-06-06. Device: Pi Zero 2 W, Raspberry Pi OS Lite 64-bit (Bookworm), kernel `6.12.87+rpt-rpi-v8`.
Hostname `walkman-a` (`brew@walkman-a.local`), driven from the Mac over passwordless SSH (ed25519).

## TL;DR

Both genuinely-uncertain foundations work:
- **Audio:** RT5645 ALSA card (`card 1: aiyvoicebonnet`) — **internal speaker AND 3.5mm headphone jack** both produce audible, correct-stereo output. Auto-loads on boot; mixer state persists.
- **LED:** bound kernel driver at `/sys/class/leds/ktd202x:led1..4`. **led1=red, led2=green, led3=blue** (led4 unused). Amber = red+green.

## The real hardware picture (differs from the build brief)

- Codec at i2c `0x1a` is a **Realtek RT5645** (not the v1-HAT "voicehat" dummy codec). The `googlevoicehat-soundcard` overlay is for the v1 HAT and does **not** work here.
- The bonnet **EEPROM** auto-declares the device-tree nodes: `rt5645@1a`, `aiy-io-i2c@52` (the AIY IO MCU, `google,aiy-io-i2c`), `ktd2026@31`.
- Both the **sound card** and the **LED** depend on the `aiy-gpio` supplier provided by the `aiy-io-i2c` driver. The stock kernel ships **none** of these drivers, so everything sat in deferred-probe limbo (this is the real cause behind the brief's "waiting_for_supplier" note — a missing driver, not a regulator).

## What was installed (the fix)

Built the AIY driver stack from the community fork that ported Google's official AIY
drivers to kernel 6.12:

- Repo: `HorseyofCoursey/trixie-aiyprojects`
- **Pinned commit: `4ee62cedb2b0756e3503a11585a7064f7edd0871`** (2026-04-05)
- Reviewed: changes are mechanical kernel-6.12 API fixes over Google's originals (fork-of-fork: google → viraniac → HorseyofCoursey). No network/file/usermode calls in the drivers; the one `request_firmware_direct` is an admin-only sysfs MCU-flash path, unused by us. The `rt5645.c.patch` is mainline codec + small jack-detect tweaks.

Three DKMS packages built via `dpkg-buildpackage -b -us -uc` and installed:
- `aiy-dkms` → `aiy-io-i2c` (+ `gpio/pwm/adc-aiy-io`) — provides `aiy-gpio`
- `aiy-voicebonnet-soundcard-dkms` → `rt5645`, `rl6231`, `snd-aiy-voicebonnet` (+ ALSA UCM2 in `/usr/share/alsa/ucm2/aiy-voicebonnet/`)
- `leds-ktd202x-dkms` → bound KTD2026 LED driver

The built `.deb`s are checkpointed in `../drivers/prebuilt/` (DKMS source debs — they contain the patched source, so installing them reproduces this exact result).

### Local patches required (carried in `../drivers/patches/`, must go into setup.sh)

1. `leds-ktd202x-probe-6.12.patch` — the fork missed the i2c `.probe` signature change for kernel 6.6+ (the LED file only). Drop the `const struct i2c_device_id *id` arg, use `client->name`. Without it, `leds-ktd202x` fails to compile.
2. **Dependency fix** in all three `debian/control`: `Depends: raspberrypi-kernel-headers` → `linux-headers-rpi-v8 | linux-headers-rpi-2712 | raspberrypi-kernel-headers`. The old package name is stale (6.1-era) on this OS; without this, apt goes into a broken state. (See `control-deps.md`.)

## config.txt changes (backup at `/boot/firmware/config.txt.bak-walkman` on the Pi)

- `dtparam=audio=on` → **commented** (disable built-in bcm2835 audio).
- Removed an earlier wrong `dtoverlay=googlevoicehat-soundcard` line.
- **No bonnet overlay line needed** — the EEPROM provides the DT nodes; the DKMS drivers bind to them.

## Audio specifics

- Card id: `aiyvoicebonnet` (currently `card 1`). PCM device: `plughw:1,0`.
- **Critical gotcha (cost the most time):** the 3.5mm headphone output is muted until you enable **`Headphone Channel Switch`**. The bonnet's own UCM `EnableSequence` turns on `Speaker Channel Switch` but **not** the headphone one — so the HP path is fully powered (DAPM "HP amp On", HPOL/HPOR On) yet silent. Enabling `Headphone Channel Switch` fixed it.
- Baseline mixer (saved with `sudo alsactl store`, restored on boot via `alsa-restore`):
  - `Speaker Switch` on, `Speaker Channel Switch` on, `Speaker Playback Volume` 45
  - `Headphone Switch` on, `Headphone Channel Switch` on, `Headphone Playback Volume` 30
  - `DAC1 Playback Volume` 60,60
  - plus the RT5645 HP/SPK routing from the UCM HiFi `EnableSequence`.
- **Jack detection works:** input device `event2` = "aiy-voicebonnet Headphone Jack".
  Query state: `sudo evtest --query /dev/input/event2 EV_SW SW_HEADPHONE_INSERT` (exit 10 = inserted, 0 = not).
- **Speaker is NOT auto-muted when headphones are inserted** — the machine driver only powers the Headphone pin on detect. The "speaker low, headphones override" behavior must be implemented in the controller (watch `event2`, toggle `Speaker Switch`).

## LED specifics

- Bound driver path (preferred): `echo <0-255> > /sys/class/leds/ktd202x:ledN/brightness` (needs root).
- **Color map: led1=red, led2=green, led3=blue. led4 unused** (KTD2026 = 3 channels). Amber = led1≈200 + led2≈120.
- **Raw I2C fallback:** with the driver bound, normal i2c to `0x31` returns "Device or resource busy"; only `i2cget -y -f 1 0x31 ...` (forced) works, and it contends with the driver. To use raw I2C you'd unload `leds_ktd202x` first. Bound driver is the production path; raw I2C is documented fallback only.

## Verified-after-reboot

card auto-loads ✓, LED driver present ✓, modules auto-load ✓, mixer state restored ✓, tone audible from both outputs ✓ (user-confirmed by ear).

## Build environment on the Pi

- Source clone: `~/walkman-build/aiy-src` (git, checked out at the pinned commit).
- Build deps installed: `dkms debhelper dh-dkms build-essential bc evtest`. Kernel headers `linux-headers-rpi-v8` (6.12.87) already present; `/lib/modules/$(uname -r)/build` symlink valid; 511MB swap.

## Next steps

Step 1: Mopidy + Mopidy-YTMusic; YouTube Music OAuth walkthrough (prove it streams end-to-end with the test account; fall back to cookie auth if needed). Then autostart, gestures, LED status, README + hardening. See `PLAN.md`.
