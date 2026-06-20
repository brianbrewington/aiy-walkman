"""Walkman CPX satellite firmware.

Copy to CIRCUITPY/code.py after installing cpx/boot.py. The Pi talks on the
usb_cdc data port:
  CPX -> Pi: "A" / "B" button press lines
  Pi -> CPX: "L:<0-255>" VU level, "V:<0-100>" volume, "C:<b>,<night_mode_b>,<seconds>"
  Pi -> CPX: "Q" status query
  CPX -> Pi: "S:<night>,<mode>,<volume>,<level>" logical display state

The protocol/rendering logic is deliberately importable on a normal computer so the
interface can be unit-tested without a physical CPX.
"""
import time

NUM_PIXELS = 10
DEBOUNCE_S = 0.08
RENDER_S = 0.03

BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)   # loudness half (pixels 0..4)
RED = (255, 0, 0)         # bass half (pixels 9..5, mirrored)
OFF = (0, 0, 0)

# Spectrum mode: one color per band, low audio freq -> high, mapped to the visible
# spectrum (red = low, violet = high). Brightness encodes each band's energy.
SPECTRUM_PALETTE = (
    (255, 0, 0),     # 0  red      (bass)
    (255, 80, 0),    # 1  orange
    (255, 180, 0),   # 2  amber
    (200, 255, 0),   # 3  yellow-green
    (0, 255, 0),     # 4  green
    (0, 255, 160),   # 5  teal
    (0, 200, 255),   # 6  cyan
    (0, 60, 255),    # 7  blue
    (110, 0, 255),   # 8  indigo
    (200, 0, 255),   # 9  violet   (treble)
)

# Flip this constant if the physical switch direction feels backwards after mounting.
NIGHT_WHEN_SWITCH_VALUE = True


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def scale(color, amount):
    amount = clamp(amount, 0.0, 1.0)
    return (int(color[0] * amount), int(color[1] * amount), int(color[2] * amount))


class CpxApp:
    def __init__(self, serial, pixels, button_a, button_b, slide, monotonic=None):
        self.serial = serial
        self.pixels = pixels
        self.button_a = button_a
        self.button_b = button_b
        self.slide = slide
        self.monotonic = monotonic or time.monotonic

        self.normal_brightness = 0.35
        self.night_mode_volume_brightness = 0.004
        self.volume_feedback_seconds = 2.0
        self.current_level = 0       # loudness 0..255 (the left/0..4 half)
        self.current_bass = 0        # bass 0..255 (the right/9..5 half)
        self.current_bands = [0] * NUM_PIXELS   # spectrum: per-band energy 0..255
        self.meter_style = "split"   # "split" | "spectrum"; set by the last frame received
        self.current_volume = 0
        self.volume_until = 0.0
        self.line_buffer = ""
        self.last_pixels = None
        self.last_a = False
        self.last_b = False
        self.last_a_time = -DEBOUNCE_S
        self.last_b_time = -DEBOUNCE_S
        self.last_render = 0.0

    def night_mode(self):
        return self.slide.value == NIGHT_WHEN_SWITCH_VALUE

    def display_mode(self):
        if self.monotonic() < self.volume_until:
            return "volume"
        if self.night_mode():
            return "off"
        return "spectrum" if self.meter_style == "spectrum" else "vu"

    def status_line(self):
        return "S:{},{},{},{}".format(
            1 if self.night_mode() else 0,
            self.display_mode(),
            clamp(int(self.current_volume), 0, 100),
            clamp(int(self.current_level), 0, 255),
        )

    def write_line(self, text):
        try:
            self.serial.write((text + "\n").encode("ascii"))
        except Exception:
            pass

    def show(self, colors):
        if colors == self.last_pixels:
            return
        for i, color in enumerate(colors):
            self.pixels[i] = color
        self.pixels.show()
        self.last_pixels = list(colors)

    def render_volume(self):
        brightness = (
            self.night_mode_volume_brightness
            if self.night_mode()
            else min(self.normal_brightness, 0.22)
        )
        lit = clamp(self.current_volume, 0, 100) * NUM_PIXELS / 100
        whole = int(lit)
        partial = lit - whole
        if self.current_volume > 0 and whole == 0:
            whole = 1
            partial = 0

        colors = []
        for i in range(NUM_PIXELS):
            if i < whole:
                amount = 1.0
            elif i == whole and i < NUM_PIXELS:
                amount = partial
            else:
                amount = 0.0
            colors.append(scale(BLUE, brightness * amount))
        self.show(colors)

    def _fill_half(self, colors, indices, level, color):
        """Light a half from indices[0] outward, proportional to level (0..255),
        with a partial last pixel. Scaled by normal_brightness."""
        lit = clamp(level, 0, 255) * len(indices) / 255
        whole = int(lit)
        partial = lit - whole
        for j, idx in enumerate(indices):
            if j < whole:
                amount = 1.0
            elif j == whole:
                amount = partial
            else:
                amount = 0.0
            if amount > 0.0:
                colors[idx] = scale(color, self.normal_brightness * amount)

    def render_vu(self):
        # Two-sided meter: loudness (magenta) grows 0->4, bass (red) grows 9->5.
        if self.night_mode():
            self.show([OFF] * NUM_PIXELS)
            return
        half = NUM_PIXELS // 2
        colors = [OFF] * NUM_PIXELS
        self._fill_half(colors, list(range(half)), self.current_level, MAGENTA)
        self._fill_half(colors, list(range(NUM_PIXELS - 1, half - 1, -1)), self.current_bass, RED)
        self.show(colors)

    def render_spectrum(self):
        # 10-band spectrum: pixel i = band i, hue from the palette (red bass -> violet
        # treble), brightness = that band's energy.
        if self.night_mode():
            self.show([OFF] * NUM_PIXELS)
            return
        colors = []
        for i in range(NUM_PIXELS):
            amount = clamp(self.current_bands[i], 0, 255) / 255
            colors.append(scale(SPECTRUM_PALETTE[i], self.normal_brightness * amount))
        self.show(colors)

    def handle_line(self, line):
        line = line.strip()
        if line.startswith("F:"):
            # F:<b0>,<b1>,...,<b9> — the 10-band spectrum, each 0..255
            try:
                parts = line[2:].split(",")
                if len(parts) == NUM_PIXELS:
                    for i in range(NUM_PIXELS):
                        self.current_bands[i] = clamp(int(parts[i]), 0, 255)
                    self.meter_style = "spectrum"
            except (ValueError, IndexError):
                pass
        elif line.startswith("M:"):
            # M:<loud>,<bass> — the two-sided meter (loudness + bass), each 0..255
            try:
                loud_s, bass_s = line[2:].split(",")
                self.current_level = clamp(int(loud_s), 0, 255)
                self.current_bass = clamp(int(bass_s), 0, 255)
                self.meter_style = "split"
            except (ValueError, IndexError):
                pass
        elif line.startswith("L:"):
            try:
                self.current_level = clamp(int(line[2:]), 0, 255)  # legacy single bar
            except ValueError:
                pass
        elif line.startswith("V:"):
            try:
                self.current_volume = clamp(int(line[2:]), 0, 100)
                self.volume_until = self.monotonic() + self.volume_feedback_seconds
            except ValueError:
                pass
        elif line.startswith("C:"):
            try:
                parts = [float(p) for p in line[2:].split(",")]
                self.normal_brightness = clamp(parts[0], 0.0, 1.0)
                self.night_mode_volume_brightness = clamp(parts[1], 0.0, 1.0)
                self.volume_feedback_seconds = max(0.1, parts[2])
            except (IndexError, ValueError):
                pass
        elif line == "X":
            self.show([OFF] * NUM_PIXELS)
        elif line == "Q":
            self.write_line(self.status_line())

    def poll_serial(self):
        try:
            waiting = self.serial.in_waiting
        except Exception:
            waiting = 0
        if not waiting:
            return
        try:
            data = self.serial.read(waiting)
        except Exception:
            return
        if not data:
            return
        self.line_buffer += data.decode("ascii", "ignore")
        while "\n" in self.line_buffer:
            line, self.line_buffer = self.line_buffer.split("\n", 1)
            self.handle_line(line)
        # Bound the residual partial line. Protocol lines are short (<40 chars); a host
        # flood without a newline must not grow line_buffer until MemoryError on the 32KB
        # SAMD21. 128 leaves ample headroom for any real line.
        if len(self.line_buffer) > 128:
            self.line_buffer = ""

    def tick(self):
        now = self.monotonic()

        pressed_a = self.button_a.value
        if pressed_a and not self.last_a and (now - self.last_a_time) >= DEBOUNCE_S:
            self.write_line("A")
            self.last_a_time = now
        self.last_a = pressed_a

        pressed_b = self.button_b.value
        if pressed_b and not self.last_b and (now - self.last_b_time) >= DEBOUNCE_S:
            self.write_line("B")
            self.last_b_time = now
        self.last_b = pressed_b

        self.poll_serial()

        if (now - self.last_render) >= RENDER_S:
            if now < self.volume_until:
                self.render_volume()
            elif self.meter_style == "spectrum":
                self.render_spectrum()
            else:
                self.render_vu()
            self.last_render = now


def make_hardware_app():
    import board
    import digitalio
    import neopixel
    import usb_cdc

    serial = usb_cdc.data or usb_cdc.console
    # Don't let a frozen/slow Pi block the render loop: time-bound writes (write_line
    # swallows the resulting timeout and drops the frame) and never block on reads.
    try:
        serial.timeout = 0
        serial.write_timeout = 0.05
    except Exception:
        pass
    pixels = neopixel.NeoPixel(board.NEOPIXEL, NUM_PIXELS, brightness=1.0, auto_write=False)

    button_a = digitalio.DigitalInOut(board.BUTTON_A)
    button_a.switch_to_input(pull=digitalio.Pull.DOWN)
    button_b = digitalio.DigitalInOut(board.BUTTON_B)
    button_b.switch_to_input(pull=digitalio.Pull.DOWN)
    slide = digitalio.DigitalInOut(board.SLIDE_SWITCH)
    slide.switch_to_input(pull=digitalio.Pull.UP)

    return CpxApp(serial, pixels, button_a, button_b, slide)


def main():
    app = make_hardware_app()
    app.show([OFF] * NUM_PIXELS)
    while True:
        app.tick()
        time.sleep(0.005)


if __name__ == "__main__":
    main()
