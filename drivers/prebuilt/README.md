# Prebuilt AIY driver DKMS packages (checkpoint)

These are the exact DKMS source `.deb`s built and installed during Step 0 that bring
up the AIY Voice Bonnet audio (RT5645) + button LED (KTD2026) on kernel
`6.12.87+rpt-rpi-v8`.

- Source: fork `HorseyofCoursey/trixie-aiyprojects`, **pinned commit `4ee62cedb2b0756e3503a11585a7064f7edd0871`**
- With local patches applied (see ../patches/): LED probe signature + control dependency.
- These are arch-`all` DKMS *source* packages — the kernel modules compile at install
  time against the running kernel's headers.

## Install (order matters: aiy first — it provides the aiy-gpio supplier)

Run this from `drivers/prebuilt/` if you are installing the driver checkpoint by
hand. Normal fresh-unit installs should just run the repo-root `setup.sh`.

```bash
sudo apt-get install -y dkms build-essential bc linux-headers-rpi-v8
sudo dpkg -i aiy-dkms_2.0-1.2_all.deb \
             aiy-voicebonnet-soundcard-dkms_3.0-1.3_all.deb \
             leds-ktd202x-dkms_1.2-2_all.deb
sudo apt-get -f install -y   # satisfy any remaining deps
```

Then disable built-in audio and reboot:

```bash
sudo sed -i 's/^dtparam=audio=on/#dtparam=audio=on/' /boot/firmware/config.txt
sudo reboot
```

After reboot verify: `aplay -l | grep aiy` and `ls /sys/class/leds/ | grep ktd`.

> These are a convenience checkpoint. `setup.sh` installs them automatically for the
> normal path. The reproducible-from-source path is still documented in
> `docs/WORKLOG.md`; if the kernel is updated, DKMS rebuilds automatically, and if
> the fork's patches ever stop applying, rebuild from a compatible source or pin/hold
> the kernel.
