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

    def test_volume_feedback_expires_back_to_green_vu(self):
        app, _serial, pixels, _a, _b, _slide, clock = make_app(now=20.0)

        app.handle_line("V:30")
        clock[0] = 20.05
        app.tick()
        self.assertEqual(pixels.values[:3], [(0, 0, int(255 * 0.22))] * 3)

        app.current_level = 255
        clock[0] = 22.10
        app.tick()
        green = (0, int(255 * 0.35), 0)
        self.assertEqual(pixels.values, [green] * cpx.NUM_PIXELS)

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
