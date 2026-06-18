# Bringing up the Circuit Playground Express satellite on a new unit

> **What this is.** A copy-pasteable runbook for flashing and provisioning the
> Adafruit **Circuit Playground Express (CPX)** that acts as the Walkman's
> volume + VU-meter satellite. This is the *board-level* procedure (firmware,
> CircuitPython, the RAM gotcha); the *design* lives in
> [`CPX-VOLUME-PLAN.md`](CPX-VOLUME-PLAN.md) and the build *story* in
> [`WORKLOG.md`](WORKLOG.md) §15.
>
> **First done:** 2026-06-17 on **`walkman-b`** (Pi Zero 2 W). Times/IDs below are
> from that run.

---

## 0. The board, and why it needs special handling

- **Hardware:** Adafruit Circuit Playground Express — **SAMD21G18A**, **256 KB
  flash, 32 KB RAM**. UF2 bootloader `v3.6.0`, Board-ID `SAMD21G18A-CPlay-v0`.
  USB IDs: `239A:8019` (CircuitPython runtime), `239A:0018` (CPLAYBOOT bootloader).
- **One USB cable** to the Pi carries power **and** bidirectional serial.
- **Two reasons this board is fussy:**
  1. **It needs CircuitPython ≥ 7.0.** Our [`cpx/boot.py`](../cpx/boot.py) calls
     `usb_cdc.enable(console=True, data=True)` to expose a *second* CDC serial
     channel for the data protocol. `usb_cdc` did not exist before CircuitPython
     7.0. Boards often ship with something much older (walkman-b shipped a 2020
     **6.1.0-beta.2** demo build).
  2. **32 KB RAM can't compile our `code.py` on-device.** Under CircuitPython 10,
     free heap is ~15 KB. Compiling the ~7 KB [`cpx/code.py`](../cpx/code.py) from
     source on the board overflows the heap → `MemoryError`. The fix is to
     **precompile to `.mpy`** (off-device, with `mpy-cross`) and ship a tiny
     launcher. See §3.

## What you need on the Pi

- `brew` in the **`dialout`** group (serial port access) and **passwordless
  `sudo`** (to mount the CIRCUITPY/CPLAYBOOT FAT volumes). On the standard image
  both are already true.
- `curl`, `unzip` (stock). **`pyserial` is NOT required** — CircuitPython USB CDC
  ignores baud, so you can talk to it with plain `echo`/`cat` (see §4) or the
  stdlib-`termios` helper pattern used during bring-up.

---

## 1. Identify the board and current firmware

With the CPX plugged into the Pi:

```bash
lsusb | grep -i 239a                        # 239a:8019 = CircuitPython runtime
ls -l /dev/serial/by-id/                    # ...CircuitPlayground_Express...-if00 -> ttyACM0
ls -l /dev/disk/by-label/CIRCUITPY          # -> /dev/sdaN (vfat, the code drive)
```

Read the running version without disturbing it (mount read-only):

```bash
sudo mkdir -p /mnt/circuitpy
sudo mount -o ro "$(readlink -f /dev/disk/by-label/CIRCUITPY)" /mnt/circuitpy
cat /mnt/circuitpy/boot_out.txt             # e.g. "Adafruit CircuitPython 6.1.0-beta.2 ..."
sudo umount /mnt/circuitpy
```

If `boot_out.txt` shows **< 7.0**, do §2. If it's already ≥ 7.0 (and ideally
matches the version your `.mpy` files were compiled for — see §3), skip to §3.

> **Stock-board note.** A fresh CPX typically shows only **one** serial interface
> (`if00`, the console). The second data interface (`if02`) appears only after our
> `boot.py` runs on a CircuitPython ≥ 7 — see §3.

---

## 2. Upgrade CircuitPython (requires a physical double-tap)

This is the one step that needs hands on the board.

**Download the UF2 on the Pi** (this resolves "latest" automatically; pin a
version if you prefer reproducibility — walkman-b used **10.2.1**):

```bash
mkdir -p ~/cpx-stage && cd ~/cpx-stage
CP_VER=$(basename "$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
  https://github.com/adafruit/circuitpython/releases/latest)")   # e.g. 10.2.1
curl -fsSL -o cp-cpx.uf2 \
  "https://downloads.circuitpython.org/bin/circuitplayground_express/en_US/adafruit-circuitpython-circuitplayground_express-en_US-${CP_VER}.uf2"
# sanity: a real UF2 starts with the magic bytes "UF2\n"
python3 -c 'import sys; sys.exit(0 if open("cp-cpx.uf2","rb").read(4)==b"UF2\n" else 1)' \
  && echo "UF2 OK ($CP_VER)"
```

**Enter the bootloader:** *double-tap the center **RESET** button* (between the A
and B buttons). All NeoPixels turn **green** and the drive re-mounts as
**`CPLAYBOOT`**. (A single tap just reboots — tap faster.)

**Flash it** (the bootloader writes the UF2 and auto-reboots into the new
CircuitPython, re-mounting as `CIRCUITPY`):

```bash
BOOT=$(readlink -f /dev/disk/by-label/CPLAYBOOT)     # e.g. /dev/sda
sudo mkdir -p /mnt/cplayboot && sudo mount "$BOOT" /mnt/cplayboot
cat /mnt/cplayboot/INFO_UF2.TXT                       # confirm "CPlay Express"
sudo cp ~/cpx-stage/cp-cpx.uf2 /mnt/cplayboot/ && sync
sudo umount /mnt/cplayboot 2>/dev/null || true       # it auto-ejects on flash
sleep 6
cat "$(readlink -f /dev/disk/by-label/CIRCUITPY 2>/dev/null)" >/dev/null 2>&1; \
sudo mount -o ro "$(readlink -f /dev/disk/by-label/CIRCUITPY)" /mnt/circuitpy && \
  cat /mnt/circuitpy/boot_out.txt && sudo umount /mnt/circuitpy   # confirm new version
```

> Flashing CircuitPython **keeps the CIRCUITPY filesystem** — the old `code.py`,
> `lib/`, and any stale `.mpy` survive and will be overwritten/replaced in §3.

---

## 3. Stage firmware — precompiled, because 32 KB RAM

Our `code.py` is too big to compile on-device (§0.2). We compile it to a `.mpy`
**on the Pi** with the matching `mpy-cross`, and make `code.py` a 2-line launcher.

> **Why a launcher + `lib/*.mpy` and not a `code.mpy`?** CircuitPython's supervisor
> only runs `code.py` / `main.py` (a `.py` source file) as the entry point — it
> will **not** run a `code.mpy`. So the entry `code.py` stays tiny (cheap to
> compile) and `import`s the heavy logic, which *can* be a precompiled `.mpy` in
> `lib/`.

**Get `mpy-cross` matching the CircuitPython version** (Pi is `aarch64`):

```bash
cd ~/cpx-stage
# find the exact key for your version + arch, then download + chmod
KEY=$(curl -fsS 'https://adafruit-circuit-python.s3.amazonaws.com/?list-type=2&prefix=bin/mpy-cross/linux-aarch64/&max-keys=1000' \
  | tr '<' '\n' | sed -n 's/^Key>//p' | grep -E "aarch64-${CP_VER}\.static" | head -1)
curl -fsSL -o mpy-cross "https://downloads.circuitpython.org/$KEY" && chmod +x mpy-cross
./mpy-cross --version    # must report the same CircuitPython version as the board
```

**Get a version-matched `neopixel` library** (our firmware's only external dep;
`adafruit_pixelbuf` it needs is a *built-in* core module, so `neopixel.mpy`
alone suffices). Pull it from the Adafruit bundle whose major matches CP:

```bash
CP_MAJOR=${CP_VER%%.*}
B_DATE=$(basename "$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
  https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/latest)")
curl -fsSL -o bundle.zip \
  "https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/${B_DATE}/adafruit-circuitpython-bundle-${CP_MAJOR}.x-mpy-${B_DATE}.zip"
unzip -o -j bundle.zip "*/lib/neopixel.mpy" -d . && rm bundle.zip
```

**Copy our firmware sources from the repo and compile** (run from your checkout,
or `scp` `cpx/boot.py` and `cpx/code.py` to `~/cpx-stage` first):

```bash
cd ~/cpx-stage
cp code.py wcpx.py            # the module name the launcher imports
./mpy-cross wcpx.py           # -> wcpx.mpy  (~2.7 KB)
```

You should now have in `~/cpx-stage`: `boot.py`, `wcpx.mpy`, `neopixel.mpy`
(and the `cp-cpx.uf2` / `mpy-cross` from earlier).

---

## 4. Deploy and bring up the data interface

```bash
DEV=$(readlink -f /dev/disk/by-label/CIRCUITPY)
sudo mount "$DEV" /mnt/circuitpy
sudo cp ~/cpx-stage/boot.py /mnt/circuitpy/boot.py
printf 'import wcpx\nwcpx.main()\n' | sudo tee /mnt/circuitpy/code.py >/dev/null
sudo cp ~/cpx-stage/wcpx.mpy     /mnt/circuitpy/lib/wcpx.mpy
sudo cp ~/cpx-stage/neopixel.mpy /mnt/circuitpy/lib/neopixel.mpy
sync && sudo umount /mnt/circuitpy
```

**Then hard-reset the board** so `boot.py` runs (it executes only on a *true*
reset/power-cycle, not the soft auto-reload that a file save triggers). Either
power-cycle, or from the **console** REPL (`/dev/ttyACM0`) send `Ctrl-C` then
`import microcontroller; microcontroller.reset()`. After it comes back you should
have **two** serial devices:

```bash
ls -l /dev/serial/by-id/    # ...-if00 -> ttyACM0 (console)  AND  ...-if02 -> ttyACM1 (DATA)
```

`if02 → ttyACM1` is the data channel the Pi service uses. If you only see `if00`,
`boot.py` didn't run (you did a soft reload, not a hard reset) — reset again.

### Verify the protocol (no pyserial needed)

The data channel speaks lines. In one terminal watch it, in another poke it:

```bash
# terminal A — watch what the CPX emits (status, button presses)
cat /dev/ttyACM1
# terminal B — query + drive the display
printf 'Q\n'      > /dev/ttyACM1     # -> "S:<night>,<mode>,<vol>,<level>"
printf 'V:65\n'   > /dev/ttyACM1     # blue volume bar
printf 'L:200\n'  > /dev/ttyACM1     # green VU level (only when slide switch is in DAY)
```

Press **A** / **B** and you should see `A` / `B` lines appear in terminal A.
Full protocol reference is in [`CPX-VOLUME-PLAN.md`](CPX-VOLUME-PLAN.md).

> **⚠ Testing-feedback trap (cost us ~an hour on walkman-b).** The firmware gives
> **no local light** when a button is pressed — it just emits a serial byte. And a
> one-shot SSH "listen for N seconds" command shows you nothing until it *returns*,
> so you can't tell when the window is live. Result: every press looked like
> "nothing works" when the buttons were fine. **Test interactively, not blind:**
> run the listener in the background (so the prompt to press appears immediately),
> and/or load a temporary `code.py` that lights the ring on press (red=A, blue=B)
> for instant on-device confirmation. The slide switch is a good "is my test even
> running?" canary because it's a latching input.

---

## 5. Make it autostart (udev + systemd)

Bring-up above is manual. For a provisioned unit, install the udev rule and
service (normally done by [`../setup.sh`](../setup.sh)):

- [`config/99-walkman-cpx.rules`](../config/99-walkman-cpx.rules) — symlinks the
  **`if02`** data interface to a stable **`/dev/walkman-cpx`** (matches
  `bInterfaceNumber=="02"`), which `config/walkman.toml [satellite] device` points at.
- [`systemd/walkman-satellite.service`](../systemd/walkman-satellite.service) — runs
  `src/walkman/satellite.py`.

```bash
sudo cp config/99-walkman-cpx.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger
ls -l /dev/walkman-cpx        # -> ttyACM1
sudo cp systemd/walkman-satellite.service /etc/systemd/system/
sudo systemctl enable --now walkman-satellite.service
```

The **green VU meter** additionally needs Mopidy running and the ALSA loopback
capture device (`plughw:CARD=Loopback,DEV=1`) producing audio — i.e. the unit must
be playing music. Volume buttons + the blue bar work without any of that.

---

## Reference card

**Device paths**

| Path | Meaning |
|---|---|
| `/dev/ttyACM0` (`if00`) | CircuitPython **console / REPL** |
| `/dev/ttyACM1` (`if02`) | **data** channel (the satellite protocol) — only after `boot.py` |
| `/dev/walkman-cpx` | udev symlink → the `if02` data device |
| `/dev/disk/by-label/CIRCUITPY` | the code drive (vfat) when running CircuitPython |
| `/dev/disk/by-label/CPLAYBOOT` | the bootloader drive when double-tapped |

**Files on CIRCUITPY after provisioning**

```
boot.py            # usb_cdc.enable(console=True, data=True)
code.py            # 2-line launcher: import wcpx; wcpx.main()
lib/wcpx.mpy       # precompiled cpx/code.py
lib/neopixel.mpy   # version-matched bundle lib
```

**REPL control bytes** (on the console channel, raw): `Ctrl-C` = interrupt running
code → REPL; `Ctrl-D` = run / soft-reboot (re-runs `code.py`, does **not** re-run
`boot.py`); `Ctrl-E` = paste mode (reliable for multi-line); `Ctrl-A` = raw REPL.

**Tuned values (field-tested on walkman-b)**

- `night_mode_volume_brightness = 0.004` → blue **1/255**, the dimmest visible
  glow (anything lower rounds to off). Set in `cpx/code.py`, `satellite.py`, and
  `config/walkman.toml`.
- Volume clamps to `[0, 70]` — the kid-safe cap.

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `MemoryError` in `code.py` output | Compiling 7 KB source on 32 KB RAM. Ship the launcher + `lib/wcpx.mpy` (§3), don't put the full source in `code.py`. |
| Only `if00` appears, no `if02`/`ttyACM1` | `boot.py` didn't run, **or** the `microcontroller.reset()` raced the auto-reload triggered by your file write. Let the post-write auto-reload settle (~3 s) *then* hard-reset. Confirm with `usb_cdc.data` at the REPL — `None` means `boot.py`'s `usb_cdc.enable()` didn't take. |
| CIRCUITPY mounts **read-only**, `cp` fails `Read-only file system` | Corrupt FAT — usually from resetting/unplugging without `sync && umount`. CircuitPython itself can't write either (`OSError [Errno 30]` at the REPL). **Repair:** at the REPL `import storage; storage.erase_filesystem()` (reformats — **wipes** boot.py/code.py/lib), then redeploy §4. Always `sync && sudo umount` the drive *before* resetting the CPX. |
| `ImportError: no module named 'binascii'` | The samd21 CircuitPython build is minimal and omits `binascii`/`ubinascii`. Don't transfer files by base64-over-REPL; mount CIRCUITPY and copy, or repair the FS first (row above). |
| `Permission denied` opening `/dev/ttyACM*` | User not in `dialout`. `sudo usermod -aG dialout $USER` then re-login. |
| `incompatible .mpy file` on import | `wcpx.mpy`/`neopixel.mpy` compiled for a different CircuitPython major. Recompile with the matching `mpy-cross` and pull the matching bundle (§3). |
| Buttons "do nothing" in a serial listen | Almost always the testing-feedback trap, not the buttons — see the ⚠ box in §4. |
| Writes to `/dev/ttyACM1` hang / `EAGAIN` | Nothing on the CPX is reading the data channel → `code.py` errored. Check the console (`ttyACM0`) for a traceback. |
