"""Button input: one physical button (GPIO 23) -> single / double / long gestures.

`PressPattern` is the pure press-pattern state machine (no GPIO deps) so it can be
unit-tested without hardware. `ButtonSource` wires it to a gpiozero Button.

Discrimination:
- **long**  : button held >= long_press seconds (gpiozero `when_held`); fires while
              still held, and suppresses the click that the following release would
              otherwise produce.
- **single**: one short press, with no second press within `double_window`.
- **double**: two short presses within `double_window` (3+ also counts as double).

This is the first source behind a pluggable input layer — a future serial/CPX source
can drive the same action dispatch (volume, repeat-track, etc.) without changes here.
"""
from __future__ import annotations

import threading


class PressPattern:
    """Turns short-press / long-press events into single/double/long gestures."""

    def __init__(self, on_gesture, double_window: float = 0.35, log=print):
        self.on_gesture = on_gesture
        self.double_window = double_window
        self.log = log
        self._clicks = 0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def short_press(self) -> None:
        """Call on each completed short press (press+release shorter than long)."""
        with self._lock:
            self._clicks += 1
            self._restart_timer()

    def long_press(self) -> None:
        """Call when a long hold is detected."""
        with self._lock:
            self._clicks = 0
            self._cancel_timer()
        self._emit("long")

    def _restart_timer(self) -> None:
        self._cancel_timer()
        self._timer = threading.Timer(self.double_window, self._resolve)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _resolve(self) -> None:
        with self._lock:
            n = self._clicks
            self._clicks = 0
            self._timer = None
        if n <= 0:
            return
        self._emit("single" if n == 1 else "double")

    def _emit(self, gesture: str) -> None:
        try:
            self.on_gesture(gesture)
        except Exception as e:  # never let an action error kill the input thread
            self.log(f"[button] action error for {gesture!r}: {e}")


class ButtonSource:
    """gpiozero Button -> PressPattern. Constructed on the Pi (needs GPIO)."""

    def __init__(self, pin: int, on_gesture, long_press: float = 1.2,
                 double_window: float = 0.35, bounce: float = 0.05, log=print):
        from gpiozero import Button  # lazy import: keep PressPattern importable w/o GPIO

        self.log = log
        self.pattern = PressPattern(on_gesture, double_window=double_window, log=log)
        self._held = False
        # AIY button pulls GPIO23 to ground when pressed -> pull_up=True (pressed=low).
        self.button = Button(pin, pull_up=True, hold_time=long_press, bounce_time=bounce)
        self.button.when_held = self._on_held
        self.button.when_released = self._on_released
        log(f"[button] watching GPIO{pin} (long>={long_press}s, double<{double_window}s)")

    def _on_held(self) -> None:
        self._held = True            # mark so the release isn't counted as a click
        self.log("[button] long press")
        self.pattern.long_press()

    def _on_released(self) -> None:
        if self._held:
            self._held = False
            return
        self.pattern.short_press()
