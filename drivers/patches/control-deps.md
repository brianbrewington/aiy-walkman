# Dependency fix for the AIY DKMS debian packages

The fork's `debian/control` for all three driver packages declares:

```
Depends: raspberrypi-kernel-headers, dkms (>= 1.95), ${misc:Depends}
```

On Raspberry Pi OS Bookworm (kernel 6.12.x) the headers package is
`linux-headers-rpi-v8` (Pi 5 = `linux-headers-rpi-2712`); `raspberrypi-kernel-headers`
is a stale 6.1-era candidate. Installing the debs as-is fails the dependency and
leaves apt in a broken state.

Fix applied before `dpkg-buildpackage` in each of `drivers/{aiy,sound,leds}/debian/control`:

```
Depends: linux-headers-rpi-v8 | linux-headers-rpi-2712 | raspberrypi-kernel-headers, dkms (>= 1.95), ${misc:Depends}
```

setup.sh should apply this (sed with a non-`|` delimiter, e.g. `@`) before building, e.g.:

```
sed -i "s@raspberrypi-kernel-headers, dkms@linux-headers-rpi-v8 | linux-headers-rpi-2712 | raspberrypi-kernel-headers, dkms@" debian/control
```

(The prebuilt debs in ../prebuilt already have this fix baked in.)
