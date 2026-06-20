"""CPX satellite helpers: config, volume events, and meter math."""
import unittest
from unittest import mock

import _path  # noqa: F401
from walkman import satellite
from walkman.mopidy_client import MopidyError


class SatelliteConfigTest(unittest.TestCase):
    def test_parse_configs_defaults_to_kid_safe_volume_cap(self):
        rpc, volume, sat = satellite.parse_configs({})
        self.assertEqual(rpc, "http://127.0.0.1:6680/mopidy/rpc")
        self.assertEqual(volume.step, 5)
        self.assertEqual(volume.lo, 0)
        self.assertEqual(volume.hi, 70)
        self.assertEqual(sat.device, "/dev/walkman-cpx")

    def test_satellite_volume_step_overrides_shared_step(self):
        _rpc, volume, _sat = satellite.parse_configs({
            "volume": {"step": 2, "min": 10, "max": 80},
            "satellite": {"volume_step": 7},
        })
        self.assertEqual(volume.step, 7)
        self.assertEqual(volume.lo, 10)
        self.assertEqual(volume.hi, 80)

    def test_parse_configs_keeps_meter_ceiling_above_floor(self):
        _rpc, _volume, sat = satellite.parse_configs({
            "satellite": {"meter_floor_rms": 5000, "meter_ceiling_rms": 100},
        })
        self.assertEqual(sat.meter_floor_rms, 5000)
        self.assertGreater(sat.meter_ceiling_rms, sat.meter_floor_rms)

    def test_satellite_accepts_legacy_night_volume_brightness_key(self):
        _rpc, _volume, sat = satellite.parse_configs({
            "satellite": {"night_volume_brightness": 0.05},
        })
        self.assertEqual(sat.night_mode_volume_brightness, 0.05)

    def test_build_arecord_cmd(self):
        cfg = satellite.SatelliteConfig(audio_capture_device="hw:Loopback,1",
                                        audio_rate=48000, audio_channels=1)
        self.assertEqual(satellite.build_arecord_cmd(cfg), [
            "arecord", "-q", "-D", "hw:Loopback,1", "-f", "S16_LE",
            "-c", "1", "-r", "48000", "-t", "raw",
        ])

    def test_config_line_sent_to_cpx(self):
        cfg = satellite.SatelliteConfig(brightness=0.4, night_mode_volume_brightness=0.05,
                                        volume_feedback_seconds=2.5)
        self.assertEqual(satellite.make_config_line(cfg), "C:0.400,0.050,2.500")

    def test_parse_cpx_status(self):
        status = satellite.parse_cpx_status("S:1,volume,70,128")
        self.assertTrue(status.night)
        self.assertEqual(status.mode, "volume")
        self.assertEqual(status.volume, 70)
        self.assertEqual(status.level, 128)

    def test_parse_cpx_status_clamps_ranges(self):
        status = satellite.parse_cpx_status("S:0,vu,999,-1")
        self.assertFalse(status.night)
        self.assertEqual(status.mode, "vu")
        self.assertEqual(status.volume, 100)
        self.assertEqual(status.level, 0)

    def test_parse_cpx_status_rejects_malformed_lines(self):
        for line in ("", "S:1,party,70,1", "S:2,vu,70,1", "S:1,vu,nope,1", "S:1,vu,1"):
            self.assertIsNone(satellite.parse_cpx_status(line))

    def test_discover_serial_prefers_data_interface_by_id(self):
        paths = [
            "/dev/serial/by-id/usb-Adafruit_Circuit_Playground-if00",
            "/dev/serial/by-id/usb-Adafruit_Circuit_Playground-if02",
        ]
        with mock.patch("walkman.satellite.os.path.exists", return_value=False), \
             mock.patch("walkman.satellite.glob.glob", return_value=paths):
            self.assertEqual(
                satellite.discover_serial_device("/dev/walkman-cpx"),
                "/dev/serial/by-id/usb-Adafruit_Circuit_Playground-if02",
            )


class SatelliteEventTest(unittest.TestCase):
    def test_button_a_nudges_volume_down_and_sends_level(self):
        mopidy = mock.MagicMock()
        mopidy.nudge_volume.return_value = 45
        sent = []
        logs = []

        handled = satellite.handle_cpx_event(
            "A",
            mopidy,
            satellite.VolumeConfig(step=5, lo=0, hi=70),
            sent.append,
            log_fn=logs.append,
        )

        self.assertTrue(handled)
        mopidy.nudge_volume.assert_called_once_with(-5, 0, 70)
        self.assertEqual(sent, ["V:45"])
        self.assertEqual(logs, ["rx A", "volume -> 45"])

    def test_button_b_nudges_volume_up(self):
        mopidy = mock.MagicMock()
        mopidy.nudge_volume.return_value = 55
        sent = []
        logs = []

        handled = satellite.handle_cpx_event(
            "b",
            mopidy,
            satellite.VolumeConfig(step=5, lo=0, hi=70),
            sent.append,
            log_fn=logs.append,
        )

        self.assertTrue(handled)
        mopidy.nudge_volume.assert_called_once_with(5, 0, 70)
        self.assertEqual(sent, ["V:55"])
        self.assertEqual(logs, ["rx B", "volume -> 55"])

    def test_unknown_line_is_ignored(self):
        mopidy = mock.MagicMock()
        self.assertFalse(satellite.handle_cpx_event(
            "L:99", mopidy, satellite.VolumeConfig(), mock.MagicMock()))
        mopidy.nudge_volume.assert_not_called()

    def test_status_line_is_logged_not_treated_as_volume_input(self):
        mopidy = mock.MagicMock()
        logs = []
        handled = satellite.handle_cpx_event(
            "S:1,off,45,0",
            mopidy,
            satellite.VolumeConfig(),
            mock.MagicMock(),
            log_fn=logs.append,
        )
        self.assertTrue(handled)
        mopidy.nudge_volume.assert_not_called()
        self.assertEqual(logs, [
            "rx S:1,off,45,0",
            "cpx status: night=1 mode=off volume=45 level=0",
        ])

    def test_mopidy_error_is_swallowed(self):
        mopidy = mock.MagicMock()
        mopidy.nudge_volume.side_effect = MopidyError("down")
        sent = mock.MagicMock()
        self.assertTrue(satellite.handle_cpx_event(
            "A", mopidy, satellite.VolumeConfig(), sent, log_fn=lambda _msg: None))
        sent.assert_not_called()


class VolumeCapTest(unittest.TestCase):
    def _mopidy(self, current):
        mopidy = mock.MagicMock()
        mopidy.wait_until_ready.return_value = True
        mopidy.get_volume.return_value = current
        return mopidy

    def test_clamps_restored_volume_down_to_cap(self):
        mopidy = self._mopidy(100)
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=0, hi=70), log_fn=lambda _m: None)
        mopidy.set_volume.assert_called_once_with(70)

    def test_raises_volume_up_to_floor(self):
        mopidy = self._mopidy(2)
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=10, hi=70), log_fn=lambda _m: None)
        mopidy.set_volume.assert_called_once_with(10)

    def test_leaves_in_range_volume_untouched(self):
        mopidy = self._mopidy(45)
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=0, hi=70), log_fn=lambda _m: None)
        mopidy.set_volume.assert_not_called()

    def test_skips_when_volume_unknown(self):
        mopidy = self._mopidy(None)
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=0, hi=70), log_fn=lambda _m: None)
        mopidy.set_volume.assert_not_called()

    def test_skips_when_mopidy_never_ready(self):
        mopidy = mock.MagicMock()
        mopidy.wait_until_ready.return_value = False
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=0, hi=70), log_fn=lambda _m: None)
        mopidy.get_volume.assert_not_called()
        mopidy.set_volume.assert_not_called()

    def test_clamp_failure_is_swallowed(self):
        mopidy = self._mopidy(100)
        mopidy.set_volume.side_effect = MopidyError("boom")
        # Should not raise.
        satellite.enforce_volume_cap(
            mopidy, satellite.VolumeConfig(lo=0, hi=70), log_fn=lambda _m: None)


class SerialLinkTest(unittest.TestCase):
    def test_send_line_logs_control_messages_but_not_level_frames(self):
        logs = []
        serial = mock.MagicMock()
        link = satellite.SerialLink(log_fn=logs.append)
        link.attach(serial)

        self.assertTrue(link.send_line("Q"))
        self.assertTrue(link.send_line("C:0.350,0.080,2.000"))
        self.assertTrue(link.send_line("V:45"))
        self.assertTrue(link.send_line("L:128"))

        self.assertEqual(logs, [
            "tx Q",
            "tx C:0.350,0.080,2.000",
            "tx V:45",
        ])
        serial.write.assert_any_call(b"L:128\n")


class MeterMathTest(unittest.TestCase):
    def test_rms_to_level_respects_floor_and_ceiling(self):
        self.assertEqual(satellite.rms_to_level(80, floor=80, ceiling=12000), 0)
        self.assertEqual(satellite.rms_to_level(12000, floor=80, ceiling=12000), 255)
        self.assertGreater(satellite.rms_to_level(1000, floor=80, ceiling=12000), 0)

    def test_smooth_level_moves_by_configured_fraction(self):
        self.assertEqual(satellite.smooth_level(0, 100, 0.4), 40)
        self.assertEqual(satellite.smooth_level(100, 0, 0.25), 75)

    def test_rms_fallback_accepts_s16le_data(self):
        data = b"\x00\x00\x00\x40\x00\xc0"  # 0, 16384, -16384
        self.assertGreater(satellite.rms_s16le(data), 0)


class AutoRangeMeterTest(unittest.TestCase):
    def test_below_silence_reads_zero(self):
        m = satellite.AutoRangeMeter(silence=80)
        self.assertEqual(m.level(0), 0)
        self.assertEqual(m.level(50), 0)

    def test_constant_signal_reads_dark(self):
        # no dynamics -> the adaptive window collapses -> bar goes dark (the whole point:
        # a loud-but-flat passage shouldn't pin the bar lit)
        m = satellite.AutoRangeMeter(silence=10)
        levels = [m.level(1000) for _ in range(20)]
        self.assertEqual(max(levels), 0)

    def test_transient_above_baseline_reads_high(self):
        m = satellite.AutoRangeMeter(silence=10, peak_decay=0.2, floor_creep=0.05)
        for _ in range(30):
            m.level(500)               # establish a quiet baseline
        self.assertGreater(m.level(5000), 200)   # a kick -> near full

    def test_scale_independent(self):
        for base in (5.0, 5_000_000.0):
            m = satellite.AutoRangeMeter(silence=1, peak_decay=0.2, floor_creep=0.05)
            for _ in range(20):
                m.level(base)
            self.assertGreater(m.level(base * 10), 200)


def _has_numpy():
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_numpy(), "numpy required for the FFT bass split")
class BandLevelsTest(unittest.TestCase):
    def _tone(self, hz, rate=44100, n=1764, channels=2, amp=10000):
        import numpy as np
        t = np.arange(n) / rate
        mono = (amp * np.sin(2 * np.pi * hz * t)).astype(np.int16)
        inter = np.repeat(mono, channels)        # L=R=mono, interleaved
        return inter.astype("<i2").tobytes()

    def test_bass_tone_dominates_treble_in_bass_band(self):
        _, bass_e = satellite.compute_band_levels(self._tone(60), 2, 44100, 40, 150)
        _, treb_e = satellite.compute_band_levels(self._tone(4000), 2, 44100, 40, 150)
        self.assertGreater(bass_e, treb_e * 5)

    def test_silence_is_zero(self):
        rms, bass = satellite.compute_band_levels(b"\x00\x00" * 1764 * 2, 2, 44100, 40, 150)
        self.assertEqual(rms, 0.0)
        self.assertEqual(bass, 0.0)


class LogBandEdgesTest(unittest.TestCase):
    def test_edges_are_monotonic_geometric_and_span_the_range(self):
        edges = satellite.log_band_edges(40, 16000, 10)
        self.assertEqual(len(edges), 11)
        self.assertAlmostEqual(edges[0], 40.0)
        self.assertAlmostEqual(edges[-1], 16000.0)
        self.assertTrue(all(edges[i] < edges[i + 1] for i in range(10)))
        ratios = [edges[i + 1] / edges[i] for i in range(10)]
        self.assertTrue(all(abs(r - ratios[0]) < 1e-6 for r in ratios))  # geometric


@unittest.skipUnless(_has_numpy(), "numpy required for the FFT spectrum")
class SpectrumBandsTest(BandLevelsTest):   # reuse the _tone helper
    def _bands(self, data, lo=40, hi=16000, n=10):
        edges = satellite.log_band_edges(lo, hi, n)
        idx = satellite.make_band_index(44100, 1764, edges)
        return satellite.compute_spectrum_bands(data, 2, 44100, idx, n)

    def test_low_tone_lights_low_band(self):
        bands = self._bands(self._tone(60))
        self.assertEqual(len(bands), 10)
        self.assertIn(max(range(10), key=lambda i: bands[i]), (0, 1))

    def test_high_tone_lights_high_band(self):
        bands = self._bands(self._tone(8000))
        self.assertGreaterEqual(max(range(10), key=lambda i: bands[i]), 7)

    def test_silence_all_zero(self):
        self.assertEqual(self._bands(b"\x00\x00" * 1764 * 2), [0.0] * 10)

    def test_numpy_fallback_keeps_band0_alive(self):
        import sys
        with mock.patch.dict(sys.modules, {"numpy": None}):
            bands = satellite.compute_spectrum_bands(b"\x00\x40" * 1764 * 2, 2, 44100, None, 10)
        self.assertEqual(len(bands), 10)
        self.assertGreater(bands[0], 0.0)        # band 0 = RMS fallback
        self.assertEqual(bands[1:], [0.0] * 9)


class MeterModeConfigTest(unittest.TestCase):
    def test_spectrum_mode_parses(self):
        _r, _v, sat = satellite.parse_configs({"satellite": {"meter_mode": "spectrum"}})
        self.assertEqual(sat.meter_mode, "spectrum")

    def test_invalid_mode_falls_back_to_split(self):
        _r, _v, sat = satellite.parse_configs({"satellite": {"meter_mode": "disco"}})
        self.assertEqual(sat.meter_mode, "split")

    def test_band_hi_clamped_under_nyquist(self):
        _r, _v, sat = satellite.parse_configs(
            {"satellite": {"audio_rate": 44100, "spectrum_band_hi_hz": 99999.0}})
        self.assertLess(sat.spectrum_band_hi_hz, 44100 / 2)


if __name__ == "__main__":
    unittest.main()
