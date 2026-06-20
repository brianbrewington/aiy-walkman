"""Host-side tests for the CircuitPython CPX firmware logic.

These tests do not import board/digitalio/neopixel/usb_cdc. They instantiate the
firmware state machine with fake serial, buttons, slide switch, and pixels so the
CPX protocol contract is checked without hardware.
"""
import importlib.util
from pathlib import Path
import unittest


def load_cpx_code():
    path = Path(__file__).resolve().parent.parent / "cpx" / "code.py"
    spec = importlib.util.spec_from_file_location("walkman_cpx_code", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpx = load_cpx_code()


class FakeSerial:
    def __init__(self):
        self.inbound = bytearray()
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.inbound)

    def feed(self, data):
        self.inbound.extend(data)

    def read(self, n):
        data = bytes(self.inbound[:n])
        del self.inbound[:n]
        return data

    def write(self, data):
        self.writes.append(data)


class FakePixels:
    def __init__(self, n):
        self.values = [None] * n
        self.show_count = 0

    def __setitem__(self, index, value):
        self.values[index] = value

    def show(self):
        self.show_count += 1


class FakePin:
    def __init__(self, value=False):
        self.value = value


def make_app(slide_value=False, now=1.0):
    serial = FakeSerial()
    pixels = FakePixels(cpx.NUM_PIXELS)
    button_a = FakePin(False)
    button_b = FakePin(False)
    slide = FakePin(slide_value)
    clock = [now]
    app = cpx.CpxApp(
        serial,
        pixels,
        button_a,
        button_b,
        slide,
        monotonic=lambda: clock[0],
    )
    return app, serial, pixels, button_a, button_b, slide, clock


class CpxFirmwareTest(unittest.TestCase):
    def test_import_does_not_require_circuitpython_hardware_modules(self):
        self.assertTrue(hasattr(cpx, "CpxApp"))

    def test_button_edges_emit_one_line_per_press(self):
        app, serial, _pixels, button_a, button_b, _slide, clock = make_app()

        button_a.value = True
        app.tick()
        app.tick()
        self.assertEqual(serial.writes, [b"A\n"])

        button_a.value = False
        clock[0] += 0.01
        app.tick()
        button_a.value = True
        clock[0] += 0.05
        app.tick()
        self.assertEqual(serial.writes, [b"A\n"])  # debounce suppresses this edge

        button_a.value = False
        clock[0] += 0.01
        app.tick()
        button_a.value = True
        clock[0] += 0.04
        app.tick()
        self.assertEqual(serial.writes, [b"A\n", b"A\n"])

        button_a.value = False
        button_b.value = True
        clock[0] += 0.10
        app.tick()
        self.assertEqual(serial.writes[-1], b"B\n")

    def test_partial_serial_lines_are_buffered_and_parsed(self):
        app, serial, _pixels, _a, _b, _slide, clock = make_app(now=10.0)

        serial.feed(b"L:12")
        app.poll_serial()
        self.assertEqual(app.current_level, 0)
        self.assertEqual(app.line_buffer, "L:12")

        serial.feed(b"8\nC:0.500,0.030,1.500\nV:70\n")
        app.poll_serial()
        self.assertEqual(app.current_level, 128)
        self.assertEqual(app.normal_brightness, 0.5)
        self.assertEqual(app.night_mode_volume_brightness, 0.03)
        self.assertEqual(app.volume_feedback_seconds, 1.5)
        self.assertEqual(app.current_volume, 70)
        self.assertEqual(app.volume_until, clock[0] + 1.5)

    def test_line_buffer_is_bounded_against_newlineless_flood(self):
        app, serial, *_rest = make_app()
        serial.feed(b"X" * 200)   # garbage flood, no newline
        app.poll_serial()
        # residual partial line is dropped, not grown unbounded (32KB SAMD21)
        self.assertEqual(app.line_buffer, "")
        # a valid line still parses cleanly afterward
        serial.feed(b"V:55\n")
        app.poll_serial()
        self.assertEqual(app.current_volume, 55)

    def test_status_query_reports_logical_state(self):
        app, serial, _pixels, _a, _b, _slide, clock = make_app(now=10.0)

        app.current_level = 128
        app.handle_line("V:70")
        app.handle_line("Q")
        self.assertEqual(serial.writes, [b"S:0,volume,70,128\n"])

        clock[0] = 13.0
        app.handle_line("Q")
        self.assertEqual(serial.writes[-1], b"S:0,vu,70,128\n")

    def test_status_query_reports_night_off_state(self):
        app, serial, _pixels, _a, _b, slide, _clock = make_app(slide_value=True)

        app.current_level = 200
        app.current_volume = 45
        app.handle_line("Q")
        self.assertEqual(serial.writes, [b"S:1,off,45,200\n"])

        slide.value = False
        app.handle_line("Q")
        self.assertEqual(serial.writes[-1], b"S:0,vu,45,200\n")

    def test_bad_serial_values_are_ignored(self):
        app, serial, _pixels, _a, _b, _slide, _clock = make_app()

        serial.feed(b"L:nope\nV:nope\nC:not,enough\n")
        app.poll_serial()
        self.assertEqual(app.current_level, 0)
        self.assertEqual(app.current_volume, 0)
        self.assertEqual(app.normal_brightness, 0.35)

    def test_normal_volume_renders_dim_blue_level_bar(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=False)

        app.current_volume = 70
        app.render_volume()

        bright_blue = (0, 0, int(255 * 0.22))
        self.assertEqual(pixels.values[:7], [bright_blue] * 7)
        self.assertEqual(pixels.values[7:], [cpx.OFF] * 3)

    def test_night_mode_suppresses_vu_and_keeps_volume_very_dim(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=True)

        app.current_level = 255
        app.render_vu()
        self.assertEqual(pixels.values, [cpx.OFF] * cpx.NUM_PIXELS)

        app.current_volume = 50
        app.render_volume()
        night_blue = (0, 0, int(255 * 0.004))  # 1/255 — dimmest visible glow
        self.assertEqual(night_blue, (0, 0, 1))
        self.assertEqual(pixels.values[:5], [night_blue] * 5)
        self.assertEqual(pixels.values[5:], [cpx.OFF] * 5)

    def test_volume_feedback_expires_back_to_split_meter(self):
        app, _serial, pixels, _a, _b, _slide, clock = make_app(now=20.0)

        app.handle_line("V:30")
        clock[0] = 20.05
        app.tick()
        self.assertEqual(pixels.values[:3], [(0, 0, int(255 * 0.22))] * 3)  # blue volume bar

        app.handle_line("M:255,0")
        clock[0] = 22.10
        app.tick()
        magenta = (int(255 * 0.35), 0, int(255 * 0.35))  # default brightness 0.35
        self.assertEqual(pixels.values[:5], [magenta] * 5)   # loudness half lit
        self.assertEqual(pixels.values[5:], [cpx.OFF] * 5)   # bass silent -> dark

    def test_m_line_parses_loudness_and_bass(self):
        app, serial, *_rest = make_app()
        app.handle_line("M:200,90")
        self.assertEqual(app.current_level, 200)
        self.assertEqual(app.current_bass, 90)
        app.handle_line("M:bad")            # malformed -> ignored, no crash
        self.assertEqual((app.current_level, app.current_bass), (200, 90))

    def test_two_sided_meter_loud_magenta_bass_red(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=False)
        app.normal_brightness = 1.0
        app.handle_line("M:255,255")
        app.render_vu()
        self.assertEqual(pixels.values[0:5], [cpx.MAGENTA] * 5)   # loudness on 0..4
        self.assertEqual(pixels.values[5:10], [cpx.RED] * 5)      # bass on 9..5

    def test_two_sided_meter_halves_are_independent(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=False)
        app.normal_brightness = 1.0
        app.handle_line("M:255,0")          # loud full, bass silent
        app.render_vu()
        self.assertEqual(pixels.values[0:5], [cpx.MAGENTA] * 5)
        self.assertEqual(pixels.values[5:10], [cpx.OFF] * 5)
        # bass grows from pixel 9 toward 5
        app.handle_line("M:0,255")
        app.render_vu()
        self.assertEqual(pixels.values[0:5], [cpx.OFF] * 5)
        self.assertEqual(pixels.values[5:10], [cpx.RED] * 5)

    def test_f_line_parses_spectrum_and_sets_style(self):
        app, _serial, *_rest = make_app()
        app.handle_line("F:0,28,56,85,113,141,170,198,226,255")
        self.assertEqual(app.current_bands, [0, 28, 56, 85, 113, 141, 170, 198, 226, 255])
        self.assertEqual(app.meter_style, "spectrum")
        app.handle_line("F:1,2,3")          # wrong arity -> ignored, state unchanged
        self.assertEqual(app.current_bands[0], 0)
        app.handle_line("M:10,20")          # M: flips style back to split
        self.assertEqual(app.meter_style, "split")

    def test_spectrum_renders_palette_brightness_per_pixel(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=False)
        app.normal_brightness = 1.0
        app.handle_line("F:255,0,255,0,255,0,255,0,255,0")
        app.render_spectrum()
        for i in range(cpx.NUM_PIXELS):
            expected = cpx.SPECTRUM_PALETTE[i] if i % 2 == 0 else cpx.OFF
            self.assertEqual(pixels.values[i], expected)
        # fractional brightness: band 128 -> half the palette color
        app.handle_line("F:128,128,128,128,128,128,128,128,128,128")
        app.render_spectrum()
        self.assertEqual(pixels.values[5], cpx.scale(cpx.SPECTRUM_PALETTE[5], 128 / 255))

    def test_spectrum_night_mode_is_dark(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app(slide_value=True)
        app.handle_line("F:255,255,255,255,255,255,255,255,255,255")
        app.render_spectrum()
        self.assertEqual(pixels.values, [cpx.OFF] * cpx.NUM_PIXELS)

    def test_tick_dispatches_to_spectrum_and_volume_overrides(self):
        app, _serial, pixels, _a, _b, _slide, clock = make_app(now=5.0, slide_value=False)
        app.normal_brightness = 1.0
        app.handle_line("F:255,0,0,0,0,0,0,0,0,0")
        clock[0] = 5.05
        app.tick()
        self.assertEqual(pixels.values[0], cpx.SPECTRUM_PALETTE[0])   # spectrum rendered
        app.handle_line("V:50")                                       # volume press overrides
        clock[0] = 5.10
        app.tick()
        self.assertEqual(pixels.values[0], (0, 0, int(255 * 0.22)))   # blue volume bar

    def test_status_reports_spectrum_mode(self):
        app, serial, _p, _a, _b, _slide, _clock = make_app(slide_value=False)
        app.handle_line("F:0,0,0,0,0,0,0,0,0,0")
        app.handle_line("Q")
        self.assertTrue(serial.writes[-1].startswith(b"S:0,spectrum,"))

    def test_config_line_clamps_brightness_and_duration(self):
        app, _serial, _pixels, _a, _b, _slide, _clock = make_app()

        app.handle_line("C:2.0,-1.0,0.0")
        self.assertEqual(app.normal_brightness, 1.0)
        self.assertEqual(app.night_mode_volume_brightness, 0.0)
        self.assertEqual(app.volume_feedback_seconds, 0.1)

    def test_stop_line_clears_pixels(self):
        app, _serial, pixels, _a, _b, _slide, _clock = make_app()
        app.current_level = 255
        app.render_vu()

        app.handle_line("X")
        self.assertEqual(pixels.values, [cpx.OFF] * cpx.NUM_PIXELS)


if __name__ == "__main__":
    unittest.main()
