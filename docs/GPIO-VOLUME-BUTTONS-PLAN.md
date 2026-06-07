# Walkman — Two GPIO Volume Buttons (plan)

**Status:** PLANNED · not built · 2026-06-07 · Pi Zero 2 W, AIY Voice Bonnet,
kernel 6.12.87+rpt-rpi-v8

**TL;DR.** Add **two momentary pushbuttons wired straight to spare GPIO pins** —
one volume-down, one volume-up, **one step per press**. No Circuit Playground
Express, no serial, no new services, no new Python dependencies. This is the
**minimal** volume route; folds into the existing controller process. The richer
route (a CPX satellite that also drives a NeoPixel VU meter) is documented
separately in [`CPX-VOLUME-PLAN.md`](CPX-VOLUME-PLAN.md).

---

## Context

The player has **no volume control today**. [`PLAN.md`](PLAN.md) §Key-decisions
reserved an "ALSA mixer get/set, wired but bound to no gesture" capability, but it
was never built — `controller.py` only does play/pause, next, and shutdown. The one
arcade button is already full (single = play/pause, double = next, long = shutdown),
so volume needs a **new input source**. The cheapest possible source is two physical
buttons on otherwise-unused GPIO pins, handled by the controller we already run.

### Confirmed design decisions

- **Volume backend = Mopidy software mixer.** Mopidy runs `mixer = software`
  ([`config/mopidy.conf.example:13`](../config/mopidy.conf.example)), so
  `core.mixer.set_volume` / `get_volume` (0–100) over the existing JSON-RPC
  `MopidyClient` is the clean path: output-independent (speaker **or** headphone),
  persisted by `restore_state = true`, sitting on top of the kid-safe ALSA baseline.
  No `amixer` volume juggling.
- **Buttons = one volume step per press** (no hold-to-repeat). Predictable for kids.
- **Fold into the controller process**, consistent with the "one combined process"
  RAM decision ([`PLAN.md`](PLAN.md) §Key-decisions). The controller already runs as
  root, already drives gpiozero via `ButtonSource`, and already holds a
  `MopidyClient` — adding two buttons is a handful of lines and zero new processes.

---

## Architecture

No new process, service, or dependency. The controller gains two plain gpiozero
`Button`s whose `when_pressed` callbacks nudge the Mopidy volume:

```
 vol-down button ── GPIO ──┐
                           ├─► controller (existing, root) ─► MopidyClient.nudge_volume(±step)
 vol-up   button ── GPIO ──┘        gpiozero Button.when_pressed
```

Volume state lives in Mopidy (`restore_state`), so this route stays consistent with
the CPX route if both ever coexist — they both just call `nudge_volume`.

---

## Shared groundwork (same helper the CPX route uses)

Add reusable volume helpers to **[`src/walkman/mopidy_client.py`](../src/walkman/mopidy_client.py)**,
next to the existing `get_state` / `get_current_track` helpers
(`mopidy_client.py:66-71`):

```python
def get_volume(self):
    return self.call("core.mixer.get_volume")          # int 0..100, may be None

def set_volume(self, v: int) -> int:
    v = max(0, min(100, int(v)))
    self.call("core.mixer.set_volume", volume=v)
    return v

def nudge_volume(self, delta: int, lo: int = 0, hi: int = 100) -> int:
    cur = self.get_volume()
    if cur is None:
        cur = 50
    return self.set_volume(max(lo, min(hi, cur + delta)))
```

And a `[volume]` section in
**[`config/walkman.toml`](../config/walkman.toml)**:

```toml
[volume]
step = 5            # percent per press
min = 0
max = 100           # lower for a kid-safe ceiling
down_gpio = 24      # TENTATIVE — confirm free on the actual bonnet (see below)
up_gpio = 25        # TENTATIVE — confirm free on the actual bonnet (see below)
```

---

## Changes

Only the controller and config change; **no new files**.

### `src/walkman/controller.py`

In `start()`, alongside the existing `ButtonSource(...)` construction
(`controller.py:152-158`), add two plain gpiozero buttons. Use the **same wiring
idiom** as the arcade button (`button.py:83`): internal pull-up, light debounce.
One step per press means just `when_pressed` — the `PressPattern` single/double/long
state machine is overkill here and is deliberately not used.

```python
# in Controller.start(), after ButtonSource(...)
vol_cfg = self.cfg.get("volume", {})
step = int(vol_cfg.get("step", 5))
lo, hi = int(vol_cfg.get("min", 0)), int(vol_cfg.get("max", 100))

from gpiozero import Button  # already a dep (button.py uses it)

def _vol(delta):
    def _cb():
        try:
            new = self.mopidy.nudge_volume(delta, lo, hi)
            log(f"volume -> {new}")
        except MopidyError as e:          # never let it kill the input
            log(f"volume change failed: {e}")
    return _cb

self._vol_down = Button(int(vol_cfg["down_gpio"]), pull_up=True, bounce_time=0.05)
self._vol_up   = Button(int(vol_cfg["up_gpio"]),   pull_up=True, bounce_time=0.05)
self._vol_down.when_pressed = _vol(-step)
self._vol_up.when_pressed   = _vol(+step)
```

Keep references on `self` so the `Button` objects aren't garbage-collected.
`MopidyError` is already imported in `controller.py`.

### `config/walkman.toml`

Add the `[volume]` section above (the two `*_gpio` keys plus the shared
`step`/`min`/`max`).

---

## Wiring & pin selection ⚠️ (the key uncertainty)

Each button bridges its GPIO pin to **ground**; `pull_up=True` uses the SoC's
internal pull-up, so **no external resistors** are needed (pressed = pin pulled low,
exactly like the arcade button — see [`STEP3-NOTES.md`](STEP3-NOTES.md) /
`button.py`).

**The catch: free, physically-reachable pins are not guaranteed.** The AIY Voice
Bonnet already claims a chunk of the 40-pin header and largely **covers** it:

| Pins | Used by |
|------|---------|
| GPIO2 / GPIO3 | I2C1 — codec `0x1a`, LED `0x31`, AIY MCU `0x52` |
| GPIO18–GPIO21 | I2S audio to the RT5645 |
| GPIO23 | arcade button (`config/walkman.toml [button] gpio`) |
| (possible) MCU/control lines | confirm against the bonnet overlay |

So **before committing or wiring**, on the actual Pi:

1. List claimed pins: `pinctrl get` (or `cat /sys/kernel/debug/gpio`) and check the
   bonnet overlay's reservations.
2. Pick **two pins that are both genuinely free *and* physically accessible** —
   header access may require a **stacking/pass-through header** or soldering to pads,
   since the bonnet sits over the header.
3. Tentative candidates to **verify, not assume**: **GPIO24 + GPIO25** (physical
   pins 18 & 22; adjacent, near a ground pin for easy wiring). Update
   `[volume].down_gpio` / `up_gpio` to whatever you prove free.

Document the chosen pair (and any soldering/header detail) once confirmed — in the
honest style of the other notes, so the next person isn't surprised.

---

## LED feedback (optional, default off)

The single RGB button LED is busy rendering player state via
[`led.py`](../src/walkman/led.py) `LedStatus` (green breathing / amber / blue·red
blink / white shutdown). This route has **no dedicated indicator**, and the user
didn't ask for one. A brief volume cue (e.g. a short brightness bump or a quick
white blink that auto-reverts) is a possible nice-to-have, but is **deliberately
deferred** to avoid entangling the latched-mode logic (`led.py:91-107`). If added
later, it should be a transient that never sets `_latched` and always returns to the
status mode. Default: no LED feedback — volume is confirmed by ear.

---

## Reused patterns / don't reinvent

- **gpiozero `Button` idiom** — copy `pull_up=True, bounce_time=0.05` from
  [`button.py:83`](../src/walkman/button.py); gpiozero is already a dependency.
- **`MopidyClient` volume helpers** (shared groundwork) — the entire volume
  mechanism.
- **Folds into the existing controller** — no 5th service; matches the "one combined
  process" decision ([`PLAN.md`](PLAN.md) §Key-decisions).
- **Action-error guard** — wrap callbacks like
  [`button.py:65-69`](../src/walkman/button.py) so a Mopidy hiccup can't wedge input.

---

## Gotchas / Known uncertainties

- **Free + reachable GPIO under the bonnet is the dominant unknown** — verify on
  hardware before wiring (above). This, not the code, is the risk.
- **Physical access** may need a stacking header or soldering — the bonnet covers
  the 40-pin header.
- **Keep `Button` refs on `self`** — gpiozero buttons GC silently if unreferenced,
  and their callbacks stop firing.
- **No volume indicator** on this route by design — that's the CPX route's job.
- **Contact bounce** — `bounce_time=0.05` matches the arcade button; widen if a
  cheap switch double-fires.

---

## Verification (end-to-end, on the real Pi)

1. **Shared volume path:** from the Pi, `core.mixer.get_volume` /
   `set_volume` move audible volume on **both** speaker and headphone, and the level
   survives a `systemctl restart walkman-mopidy` (`restore_state`).
2. **Buttons:** press each button → volume steps exactly one increment (confirm by
   ear and via `get_volume`); holding a button does **nothing extra**
   (one-step-per-press).
3. **No regressions:** the arcade button's play/pause / next / shutdown and the LED
   status loop are unaffected; `systemctl restart walkman-controller` comes back
   clean with all three inputs live.

---

## Trade-off vs. the CPX route

| | This route (GPIO) | CPX route |
|---|---|---|
| Extra hardware | 2 buttons + wires | CPX + 1 USB cable |
| New deps / services | none | pyserial, `snd-aloop`, new unit, CPX firmware |
| Gives | volume only | volume **+ live VU meter** |
| Main risk | finding/reaching free GPIO under the bonnet | real-time audio tap on a weak CPU |
| Effort | low | higher |

See [`CPX-VOLUME-PLAN.md`](CPX-VOLUME-PLAN.md) for the richer option.

---

## Out of scope

- No volume *gesture* on the arcade button (it stays full: single/double/long).
- Repeat-track and now-playing OLED stay in [`IDEAS.md`](IDEAS.md).
