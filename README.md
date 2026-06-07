# Walkman — one-button headless music player

A screen-free YouTube Music player for kids on a **Raspberry Pi Zero 2 W** + **Google
AIY Voice Bonnet**. Boots straight into shuffle-playing one playlist; a single arcade
button + its RGB LED are the only I/O.

> **Status (2026-06-06): Steps 0 & 1 complete.**
> - **Step 0:** bonnet audio (RT5645 — internal speaker *and* 3.5mm headphones) and
>   the button RGB LED (bound `leds-ktd202x` driver), verified on hardware incl.
>   across reboot — [`docs/STEP0-NOTES.md`](docs/STEP0-NOTES.md).
> - **Step 1:** Mopidy + Mopidy-YouTube streaming an authenticated YouTube Music
>   playlist (user's subscription) through the bonnet — proven end-to-end —
>   [`docs/STEP1-PROGRESS.md`](docs/STEP1-PROGRESS.md).
>
> Next: boot service + autoplay, button gestures, LED status, hardening — see
> [`docs/PLAN.md`](docs/PLAN.md).

## Hardware / OS
- Pi Zero 2 W, Raspberry Pi OS Lite 64-bit (Bookworm), kernel `6.12.87+rpt-rpi-v8`.
- AIY Voice Bonnet: RT5645 codec (i2c 0x1a), AIY IO MCU (0x52), KTD2026 button LED (0x31).

## Repo layout
```
docs/
  STEP0-NOTES.md     # detailed Step 0 bring-up notes / checkpoint (read this)
  PLAN.md            # the approved build plan, steps 0-5
drivers/
  prebuilt/          # checkpoint .deb DKMS packages for the AIY drivers (+ install notes)
  patches/           # local source/packaging patches required on kernel 6.12
```
(Application code, `setup.sh`, systemd units, and config land here in later steps.)

## Driver foundation (summary)
The stock kernel ships no AIY drivers. We build them from the kernel-6.12 fork
`HorseyofCoursey/trixie-aiyprojects` (pinned commit `4ee62ce`), as three DKMS packages,
with two small local patches. Full detail and the headphone-mute gotcha are in
`docs/STEP0-NOTES.md`.
