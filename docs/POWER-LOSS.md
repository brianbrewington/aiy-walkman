# Power-loss resilience (kids yank the cord)

Two layers protect the SD card from corruption when power is cut mid-write:

## Layer 1 — the safe-shutdown gesture (already built)
**Long-press the button (~1.2 s)** → the LED goes solid **white** → a clean
`systemctl poweroff`. When it's white it has flushed and halted; it's safe to pull
the cord. Teach this as "hold the button until it turns white, then unplug." This is
the everyday mitigation.

## Layer 2 — read-only root via overlayfs (strong mitigation)
Makes the root filesystem **read-only**, with all writes going to a RAM overlay that's
**discarded on reboot**. A yanked cord then can't corrupt the card — there are no
persistent writes to interrupt. Recommended for the deployed units.

### Enable
Interactive (reliable):
```
sudo raspi-config
  → 4 Performance Options → Overlay File System
      → enable the overlay file system: Yes
      → set the boot partition read-only: Yes
sudo reboot
```
Non-interactive equivalent (verify on your raspi-config version):
```
sudo raspi-config nonint enable_overlayfs
sudo reboot
```

### What breaks while read-only (by design)
Nothing persists across a reboot. So these DON'T stick until you disable the overlay:
- editing `config/walkman.toml` (playlist), `mopidy.conf`, `config.txt`
- **re-auth / the cookie-monster** — `walkman-account.sh` writes
  `~/.config/walkman/ytmusic-auth.json`, which would vanish on reboot
- `apt`/`pip` updates, new logs, `alsactl store`

### Making changes (incl. re-auth) on an overlayfs unit
```
sudo raspi-config nonint disable_overlayfs   # (or interactive: set Overlay FS = No)
sudo reboot
# ... now the FS is writable: run walkman-account.sh, edit config, update, etc. ...
sudo raspi-config nonint enable_overlayfs
sudo reboot
```

### Important interaction with cookie expiry
With overlayfs ON, a cookie refresh won't survive a reboot, so the device would lose
auth on next power-cycle. Options:
- **Simplest:** when the LED warns of expiry, do the disable→reauth→enable cycle above.
- **Better (future, see docs/IDEAS.md):** keep just the auth file (and `walkman.toml`)
  on a small **writable partition / bind-mount** that survives overlayfs, so the
  cookie-monster works without toggling. Not yet implemented.

> Tip: leave overlayfs **off** during initial provisioning + testing; turn it **on**
> only once a unit is stable and handed to a kid.
