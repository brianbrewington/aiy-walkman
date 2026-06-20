#!/usr/bin/env python3
"""CPX satellite: volume buttons + NeoPixel VU frames over USB serial.

The Circuit Playground Express handles physical UI locally:
  - Button A/B -> sends "A" / "B" lines to this process.
  - NeoPixels render "V:<volume>" as a temporary blue volume bar.
  - NeoPixels render "L:<level>" as the normal green VU meter.

This Pi-side process owns the two system-facing jobs:
  - nudge Mopidy's software mixer for CPX button presses;
  - drain an ALSA loopback capture device, compute a lightweight RMS envelope, and
    stream one 0..255 level value to the CPX.
"""
from __future__ import annotations

from dataclasses import dataclass
import collections
import glob
import math
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

try:
    import audioop as _audioop
except ImportError:  # Python 3.13+ fallback.
    _audioop = None

try:
    from walkman.mopidy_client import MopidyClient, MopidyError
except ImportError:  # allow running as a plain script (systemd ExecStart)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from walkman.mopidy_client import MopidyClient, MopidyError

DEFAULT_CONFIG = "/home/brew/walkman/config/walkman.toml"
ADAFRUIT_USB_VID = 0x239A


def log(msg: str) -> None:
    print(f"[walkman-satellite] {msg}", flush=True)


@dataclass(frozen=True)
class VolumeConfig:
    step: int = 5
    lo: int = 0
    hi: int = 70


@dataclass(frozen=True)
class SatelliteConfig:
    device: str = "/dev/walkman-cpx"
    baud: int = 115200
    level_hz: int = 25
    volume_feedback_seconds: float = 2.0
    brightness: float = 0.35
    night_mode_volume_brightness: float = 0.004
    smoothing: float = 0.4
    audio_capture_device: str = "plughw:CARD=Loopback,DEV=1"
    audio_rate: int = 44100
    audio_channels: int = 2
    audio_format: str = "S16_LE"
    meter_floor_rms: int = 80
    meter_ceiling_rms: int = 12000
    # Two-sided meter: loudness (wideband RMS) + bass (FFT energy in a low band).
    meter_bass_hz_lo: float = 40.0      # bass band low edge (Hz) — kick/sub
    meter_bass_hz_hi: float = 150.0     # bass band high edge (Hz)
    meter_bass_silence: float = 50.0    # bass-energy floor below which the bass half is dark
    # Adaptive auto-ranging (per 25Hz frame): the meter tracks the signal's own recent
    # floor/peak so neither half pins. Higher decay/creep = faster adaptation.
    meter_peak_decay: float = 0.08
    meter_floor_creep: float = 0.04
    meter_min_span_frac: float = 0.25   # min window as a fraction of peak (sensitivity)
    # Lights run ahead of the buffered speaker audio; delay the frames to re-sync.
    meter_sync_delay_ms: int = 0
    # If set, the meter loop live-reads this file's int (ms) as a delay override (for the
    # interactive tuner) — beats meter_sync_delay_ms while present. "" disables.
    meter_tuning_file: str = ""
    # Spectrum mode (meter_mode="spectrum"): 10 log-spaced FFT bands, one per pixel.
    meter_mode: str = "split"               # "split" (loud+bass) | "spectrum"
    spectrum_band_lo_hz: float = 40.0       # band 0 low edge (Hz)
    spectrum_band_hi_hz: float = 16000.0    # band 9 high edge (Hz); clamped under Nyquist
    spectrum_per_band_autorange: bool = True  # True = every band dances; False = honest shape
    spectrum_silence: float = 50.0          # per-band energy floor below which a band is dark


SPECTRUM_BANDS = 10   # one log-spaced FFT band per NeoPixel


@dataclass(frozen=True)
class CpxStatus:
    night: bool
    mode: str
    volume: int
    level: int


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def parse_configs(cfg: dict) -> tuple[str, VolumeConfig, SatelliteConfig]:
    rpc_url = cfg.get("mopidy", {}).get("rpc_url", "http://127.0.0.1:6680/mopidy/rpc")
    vol = cfg.get("volume", {})
    sat = cfg.get("satellite", {})

    step = int(sat.get("volume_step", vol.get("step", 5)))
    lo = int(vol.get("min", 0))
    hi = int(vol.get("max", 70))
    volume_cfg = VolumeConfig(step=step, lo=clamp(lo, 0, 100), hi=clamp(hi, 0, 100))
    if volume_cfg.hi < volume_cfg.lo:
        volume_cfg = VolumeConfig(step=volume_cfg.step, lo=volume_cfg.lo, hi=volume_cfg.lo)

    # Keep the meter range a non-degenerate interval (ceiling strictly above floor) so the
    # invariant lives here rather than relying on rms_to_level's runtime guard.
    meter_floor = max(1, int(sat.get("meter_floor_rms", 80)))
    meter_ceiling = max(meter_floor + 1, int(sat.get("meter_ceiling_rms", 12000)))

    satellite_cfg = SatelliteConfig(
        device=str(sat.get("device", "/dev/walkman-cpx")),
        baud=int(sat.get("baud", 115200)),
        level_hz=max(1, int(sat.get("level_hz", 25))),
        volume_feedback_seconds=float(sat.get("volume_feedback_seconds", 2.0)),
        brightness=float(sat.get("brightness", 0.35)),
        night_mode_volume_brightness=float(
            sat.get("night_mode_volume_brightness", sat.get("night_volume_brightness", 0.004))
        ),
        smoothing=clamp(float(sat.get("smoothing", 0.4)), 0.0, 1.0),
        audio_capture_device=str(sat.get("audio_capture_device", "plughw:CARD=Loopback,DEV=1")),
        audio_rate=int(sat.get("audio_rate", 44100)),
        audio_channels=max(1, int(sat.get("audio_channels", 2))),
        audio_format=str(sat.get("audio_format", "S16_LE")),
        meter_floor_rms=meter_floor,
        meter_ceiling_rms=meter_ceiling,
        meter_bass_hz_lo=float(sat.get("meter_bass_hz_lo", 40.0)),
        meter_bass_hz_hi=float(sat.get("meter_bass_hz_hi", 150.0)),
        meter_bass_silence=float(sat.get("meter_bass_silence", 50.0)),
        meter_peak_decay=clamp(float(sat.get("meter_peak_decay", 0.08)), 0.0, 1.0),
        meter_floor_creep=clamp(float(sat.get("meter_floor_creep", 0.04)), 0.0, 1.0),
        meter_min_span_frac=max(0.01, float(sat.get("meter_min_span_frac", 0.25))),
        meter_sync_delay_ms=max(0, int(sat.get("meter_sync_delay_ms", 0))),
        meter_tuning_file=str(sat.get("meter_tuning_file", "")),
        meter_mode=(str(sat.get("meter_mode", "split")).lower()
                    if str(sat.get("meter_mode", "split")).lower() in ("split", "spectrum")
                    else "split"),
        spectrum_band_lo_hz=float(sat.get("spectrum_band_lo_hz", 40.0)),
        spectrum_band_hi_hz=min(
            float(sat.get("spectrum_band_hi_hz", 16000.0)),
            int(sat.get("audio_rate", 44100)) / 2 - 1),
        spectrum_per_band_autorange=bool(sat.get("spectrum_per_band_autorange", True)),
        spectrum_silence=float(sat.get("spectrum_silence", 50.0)),
    )
    return rpc_url, volume_cfg, satellite_cfg


def build_arecord_cmd(cfg: SatelliteConfig) -> list[str]:
    return [
        "arecord",
        "-q",
        "-D",
        cfg.audio_capture_device,
        "-f",
        cfg.audio_format,
        "-c",
        str(cfg.audio_channels),
        "-r",
        str(cfg.audio_rate),
        "-t",
        "raw",
    ]


def rms_s16le(data: bytes) -> int:
    if not data:
        return 0
    if _audioop is not None:
        return int(_audioop.rms(data, 2))

    size = len(data) - (len(data) % 2)
    if size <= 0:
        return 0
    total = 0
    count = 0
    for (sample,) in struct.iter_unpack("<h", data[:size]):
        total += sample * sample
        count += 1
    return int(math.sqrt(total / count)) if count else 0


def rms_to_level(rms: int, floor: int, ceiling: int) -> int:
    """Map RMS to 0..255 with sqrt compression so quiet music still moves."""
    if rms <= floor:
        return 0
    ceiling = max(ceiling, floor + 1)
    norm = clamp((rms - floor) / (ceiling - floor), 0.0, 1.0)
    return int(round(255 * math.sqrt(norm)))


def smooth_level(previous: int, current: int, smoothing: float) -> int:
    smoothing = clamp(smoothing, 0.0, 1.0)
    return int(round(previous + (current - previous) * smoothing))


class AutoRangeMeter:
    """Map an arbitrary-scale signal to 0..255 with an adaptive window.

    A fixed [floor, ceiling] pins the bar when a track lives in a narrow band — the
    low pixels stay lit (no information) and only the top moves. Instead, track the
    signal's own recent floor (`lo`, creeps up under sustained level) and peak (`hi`,
    decays toward the floor), and map within that moving window so the bar always
    spends its full range on the *current* dynamics. Kicks pulse; swells breathe.
    Below an absolute `silence` the meter reads 0 (avoids amplifying noise on quiet).
    Coefficients are per-frame (fed at level_hz). Scale-independent (works for both
    wideband RMS and FFT band-energy) because the min window is a fraction of `hi`.
    """

    def __init__(self, silence, peak_decay=0.08, floor_creep=0.04, min_span_frac=0.25):
        self.silence = float(max(1.0, silence))
        self.peak_decay = clamp(peak_decay, 0.0, 1.0)
        self.floor_creep = clamp(floor_creep, 0.0, 1.0)
        self.min_span_frac = max(0.01, min_span_frac)
        self.lo = None
        self.hi = None

    def level(self, value: float) -> int:
        value = float(value)
        if value <= self.silence:
            if self.hi is not None and self.lo is not None:
                self.hi += (self.lo - self.hi) * self.peak_decay  # let the peak relax
            return 0
        if self.lo is None:
            self.lo = self.hi = value
        if value > self.hi:
            self.hi = value
        else:
            self.hi += (self.lo - self.hi) * self.peak_decay
        if value < self.lo:
            self.lo = value
        else:
            self.lo += (value - self.lo) * self.floor_creep
        span = self.hi - self.lo
        floor_span = self.min_span_frac * max(self.hi, 1.0)
        if span < floor_span:
            span = floor_span
        return int(round(255 * clamp((value - self.lo) / span, 0.0, 1.0)))


def compute_band_levels(data: bytes, channels: int, rate: int,
                        bass_lo_hz: float, bass_hi_hz: float):
    """Return (wideband_rms, bass_band_energy) for one S16_LE chunk.

    Uses numpy for a real FFT to isolate the bass band (kick/sub). Falls back to the
    stdlib wideband RMS with no bass (bass=0) if numpy is unavailable.
    """
    try:
        import numpy as np
    except ImportError:
        return float(rms_s16le(data)), 0.0
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return 0.0, 0.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    x = samples.astype(np.float32)
    rms = float(np.sqrt(np.mean(x * x)))
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    freqs = np.fft.rfftfreq(x.size, 1.0 / rate)
    band = (freqs >= bass_lo_hz) & (freqs <= bass_hi_hz)
    bass = float(spec[band].mean()) if band.any() else 0.0
    return rms, bass


def log_band_edges(lo_hz: float, hi_hz: float, n_bands: int) -> list:
    """n_bands+1 logarithmically-spaced band edges from lo_hz to hi_hz (geometric).

    Pure stdlib so it is testable without numpy. Log spacing matches pitch perception —
    each band spans a roughly constant musical interval.
    """
    lo = max(1.0, float(lo_hz))
    hi = max(lo + 1.0, float(hi_hz))
    ratio = (hi / lo) ** (1.0 / n_bands)
    return [lo * (ratio ** i) for i in range(n_bands + 1)]


def make_band_index(rate: int, chunk_frames: int, edges: list):
    """Map each rFFT bin to its band index (0..len(edges)-2), or -1 if outside all bands.

    Computed once per loop (depends only on rate + chunk size). Returns a numpy int array;
    returns None if numpy is unavailable.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    freqs = np.fft.rfftfreq(chunk_frames, 1.0 / rate)
    idx = np.digitize(freqs, edges) - 1            # bin -> band (0-based)
    idx[(idx < 0) | (idx >= len(edges) - 1)] = -1  # mark out-of-range bins
    return idx


def compute_spectrum_bands(data: bytes, channels: int, rate: int,
                           band_index, n_bands: int) -> list:
    """Return one mean-magnitude energy per band (len == n_bands) for an S16_LE chunk.

    Reuses the same rFFT as compute_band_levels, grouping bins by the precomputed
    band_index. numpy-missing fallback returns [rms, 0, 0, ...] so band 0 still moves.
    """
    try:
        import numpy as np
    except ImportError:
        return [float(rms_s16le(data))] + [0.0] * (n_bands - 1)
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return [0.0] * n_bands
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    x = samples.astype(np.float32)
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    if band_index is None or band_index.size != spec.size:
        band_index = make_band_index(rate, samples.size, log_band_edges(40.0, rate / 2 - 1, n_bands))
    sums = np.bincount(band_index[band_index >= 0],
                       weights=spec[band_index >= 0], minlength=n_bands)
    counts = np.bincount(band_index[band_index >= 0], minlength=n_bands)
    means = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
    return [float(v) for v in means[:n_bands]]


def make_config_line(cfg: SatelliteConfig) -> str:
    return "C:{:.3f},{:.3f},{:.3f}".format(
        clamp(cfg.brightness, 0.0, 1.0),
        clamp(cfg.night_mode_volume_brightness, 0.0, 1.0),
        max(0.1, cfg.volume_feedback_seconds),
    )


def parse_cpx_status(line: str) -> CpxStatus | None:
    if not line.startswith("S:"):
        return None
    parts = line[2:].split(",")
    if len(parts) != 4:
        return None
    night_s, mode, volume_s, level_s = parts
    if night_s not in ("0", "1") or mode not in ("off", "vu", "volume", "spectrum"):
        return None
    try:
        volume = int(volume_s)
        level = int(level_s)
    except ValueError:
        return None
    return CpxStatus(
        night=night_s == "1",
        mode=mode,
        volume=clamp(volume, 0, 100),
        level=clamp(level, 0, 255),
    )


def handle_cpx_event(line: str, mopidy: MopidyClient, volume_cfg: VolumeConfig,
                     send_line, log_fn=log) -> bool:
    raw = line.strip()
    status = parse_cpx_status(raw)
    if status is not None:
        log_fn(f"rx {raw}")
        log_fn(
            "cpx status: night={} mode={} volume={} level={}".format(
                int(status.night), status.mode, status.volume, status.level
            )
        )
        return True

    event = raw.upper()
    if event == "A":
        delta = -volume_cfg.step
    elif event == "B":
        delta = volume_cfg.step
    else:
        return False

    try:
        log_fn(f"rx {event}")
        new_volume = mopidy.nudge_volume(delta, volume_cfg.lo, volume_cfg.hi)
        send_line(f"V:{new_volume}")
        log_fn(f"volume -> {new_volume}")
    except MopidyError as e:
        log_fn(f"volume change failed: {e}")
    return True


class SerialLink:
    def __init__(self, log_fn=log):
        self._lock = threading.Lock()
        self._serial = None
        self.log = log_fn

    def attach(self, ser) -> None:
        with self._lock:
            self._serial = ser

    def detach(self, ser=None) -> None:
        with self._lock:
            if ser is None or self._serial is ser:
                self._serial = None

    @staticmethod
    def should_log_tx(line: str) -> bool:
        return not (line.startswith("L:") or line.startswith("M:") or line.startswith("F:"))

    def send_line(self, line: str) -> bool:
        if not line.endswith("\n"):
            line += "\n"
        clean = line.strip()
        data = line.encode("ascii", "replace")
        with self._lock:
            ser = self._serial
        if ser is None:
            return False
        try:
            ser.write(data)
            if self.should_log_tx(clean):
                self.log(f"tx {clean}")
            return True
        except Exception:
            self.detach(ser)
            return False


def discover_serial_device(preferred: str) -> str | None:
    if preferred and os.path.exists(preferred):
        return preferred

    by_id_matches = []
    for path in sorted(glob.glob("/dev/serial/by-id/*")):
        name = os.path.basename(path).lower()
        if "adafruit" in name or "circuit_playground" in name or "circuitplayground" in name:
            by_id_matches.append(path)
    if by_id_matches:
        data_matches = [
            path for path in by_id_matches
            if "if02" in os.path.basename(path).lower()
            or "interface_02" in os.path.basename(path).lower()
            or "interface_2" in os.path.basename(path).lower()
            or "data" in os.path.basename(path).lower()
        ]
        return (data_matches or by_id_matches)[-1]

    try:
        from serial.tools import list_ports
    except Exception:
        return None

    ports = sorted(list_ports.comports(), key=lambda p: p.device)
    data_candidates = []
    other_candidates = []
    for port in ports:
        if getattr(port, "vid", None) != ADAFRUIT_USB_VID:
            continue
        interface = (getattr(port, "interface", None) or "").lower()
        if "data" in interface or "cdc data" in interface:
            data_candidates.append(port.device)
        else:
            other_candidates.append(port.device)
    # If pyserial cannot identify the interface, prefer the last ACM endpoint; with
    # CircuitPython console+data, the data channel is normally the second one.
    return (data_candidates or other_candidates or [None])[-1]


def enforce_volume_cap(mopidy: MopidyClient, volume_cfg: VolumeConfig, log_fn=log) -> None:
    """Clamp the current Mopidy volume down into [lo, hi] once Mopidy is reachable.

    Mopidy's ``restore_state`` re-loads the last session's saved volume on startup,
    which can be above the kid-safe cap. The button handler only clamps *deltas*, so
    without this a unit could boot loud. The satellite starts before Mopidy, so we wait
    for it, then bring any out-of-range volume back inside the configured ceiling.
    """
    if not mopidy.wait_until_ready(timeout=180.0, log=log_fn):
        log_fn("volume cap: Mopidy not reachable; skipping startup clamp")
        return
    try:
        cur = mopidy.get_volume()
        if cur is not None and (cur > volume_cfg.hi or cur < volume_cfg.lo):
            target = clamp(int(cur), volume_cfg.lo, volume_cfg.hi)
            mopidy.set_volume(target)
            log_fn(f"volume cap: clamped {cur} -> {target}")
    except MopidyError as e:
        log_fn(f"volume cap: clamp failed: {e}")


def serial_loop(stop: threading.Event, link: SerialLink, mopidy: MopidyClient,
                volume_cfg: VolumeConfig, satellite_cfg: SatelliteConfig) -> None:
    while not stop.is_set():
        try:
            import serial
        except ImportError:
            log("pyserial is not installed; install python3-serial")
            stop.wait(30.0)
            continue

        device = discover_serial_device(satellite_cfg.device)
        if device is None:
            log("CPX serial device not found; retrying")
            stop.wait(2.0)
            continue

        ser = None
        try:
            ser = serial.Serial(
                device,
                satellite_cfg.baud,
                timeout=0.25,
                write_timeout=0.5,
            )
            link.attach(ser)
            link.send_line(make_config_line(satellite_cfg))
            link.send_line("Q")
            log(f"connected CPX serial: {device}")

            while not stop.is_set():
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", "ignore").strip()
                if line:
                    handle_cpx_event(line, mopidy, volume_cfg, link.send_line)
        except Exception as e:
            log(f"CPX serial error: {e}; reconnecting")
        finally:
            link.detach(ser)
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
        stop.wait(1.0)


def _read_delay_override(path: str, last_mtime, base_frames: int, frames: int, level_hz: int):
    """Live-read the interactive tuner's delay file. Returns (delay_frames, mtime).

    Re-reads only when the file's mtime changes (cheap to poll each frame). When the file
    is absent (tuner not running / quit), reverts to the toml base. Unreadable -> keep.
    """
    if not path:
        return base_frames, last_mtime
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return base_frames, None        # file gone -> back to the configured value
    if mtime == last_mtime:
        return frames, last_mtime
    try:
        with open(path) as f:
            ms = max(0, int(f.read().strip()))
    except (OSError, ValueError):
        return frames, mtime
    new_frames = max(0, int(round(ms * level_hz / 1000.0)))
    log(f"sync delay override -> {ms}ms ({new_frames} frames)")
    return new_frames, mtime


def audio_meter_loop(stop: threading.Event, link: SerialLink, cfg: SatelliteConfig) -> None:
    spectrum = cfg.meter_mode == "spectrum"
    try:
        import numpy  # noqa: F401
    except ImportError:
        log("WARNING: numpy not installed; "
            + ("spectrum reduced to band 0" if spectrum else "bass half disabled")
            + " (loudness only). Install python3-numpy.")
    chunk_frames = max(64, int(cfg.audio_rate / cfg.level_hz))
    # S16_LE only for now; keep the config explicit so future formats fail obviously.
    sample_width = 2
    chunk_bytes = chunk_frames * cfg.audio_channels * sample_width
    cmd = build_arecord_cmd(cfg)

    # Per-band/per-half adaptive meters so nothing pins on a loud-but-narrow track.
    if spectrum:
        edges = log_band_edges(cfg.spectrum_band_lo_hz, cfg.spectrum_band_hi_hz, SPECTRUM_BANDS)
        band_index = make_band_index(cfg.audio_rate, chunk_frames, edges)
        meters = [AutoRangeMeter(cfg.spectrum_silence, cfg.meter_peak_decay,
                                 cfg.meter_floor_creep, cfg.meter_min_span_frac)
                  for _ in range(SPECTRUM_BANDS)]
        smoothed = [0] * SPECTRUM_BANDS
        global_peak = [0.0]   # for the honest (non-autorange) shared-scale mode
        log(f"meter mode: spectrum ({SPECTRUM_BANDS} bands, "
            f"{cfg.spectrum_band_lo_hz:.0f}-{cfg.spectrum_band_hi_hz:.0f} Hz, "
            f"per-band autorange={cfg.spectrum_per_band_autorange})")
    else:
        loud_meter = AutoRangeMeter(cfg.meter_floor_rms, cfg.meter_peak_decay,
                                    cfg.meter_floor_creep, cfg.meter_min_span_frac)
        bass_meter = AutoRangeMeter(cfg.meter_bass_silence, cfg.meter_peak_decay,
                                    cfg.meter_floor_creep, cfg.meter_min_span_frac)
        smoothed_loud = smoothed_bass = 0

    # Re-sync the lights to the buffered speaker output: hold each frame N frames. The
    # interactive tuner can override this live via cfg.meter_tuning_file.
    base_frames = max(0, int(round(cfg.meter_sync_delay_ms * cfg.level_hz / 1000.0)))
    delay_frames = base_frames
    override_mtime = None
    delay_buf = collections.deque()

    while not stop.is_set():
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            log(f"audio meter reading {cfg.audio_capture_device}")
            assert proc.stdout is not None
            while not stop.is_set():
                data = proc.stdout.read(chunk_bytes)
                if not data:
                    break
                delay_frames, override_mtime = _read_delay_override(
                    cfg.meter_tuning_file, override_mtime, base_frames, delay_frames, cfg.level_hz)
                if spectrum:
                    bands = compute_spectrum_bands(data, cfg.audio_channels,
                                                   cfg.audio_rate, band_index, SPECTRUM_BANDS)
                    if cfg.spectrum_per_band_autorange:
                        levels = [m.level(b) for m, b in zip(meters, bands)]
                    else:
                        # honest shape: one shared peak preserves true relative band heights
                        global_peak[0] = max(max(bands), global_peak[0] * (1.0 - cfg.meter_peak_decay))
                        denom = max(global_peak[0], cfg.spectrum_silence)
                        levels = [int(round(255 * clamp(b / denom, 0.0, 1.0))) for b in bands]
                    smoothed = [smooth_level(s, lv, cfg.smoothing)
                                for s, lv in zip(smoothed, levels)]
                    delay_buf.append(tuple(smoothed))
                else:
                    rms, bass_energy = compute_band_levels(
                        data, cfg.audio_channels, cfg.audio_rate,
                        cfg.meter_bass_hz_lo, cfg.meter_bass_hz_hi)
                    smoothed_loud = smooth_level(smoothed_loud, loud_meter.level(rms), cfg.smoothing)
                    smoothed_bass = smooth_level(smoothed_bass, bass_meter.level(bass_energy), cfg.smoothing)
                    delay_buf.append((smoothed_loud, smoothed_bass))
                # Emit a frame delayed by sync_delay_ms; while-drain so a shrunk delay catches up.
                while len(delay_buf) > delay_frames:
                    out = delay_buf.popleft()
                    if spectrum:
                        link.send_line("F:" + ",".join(str(v) for v in out))
                    else:
                        link.send_line(f"M:{out[0]},{out[1]}")
            code = proc.poll()
            log(f"audio meter stopped (code={code}); retrying")
        except FileNotFoundError:
            log("arecord is not installed; install alsa-utils")
            stop.wait(30.0)
        except Exception as e:
            log(f"audio meter error: {e}; retrying")
            stop.wait(2.0)
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        stop.wait(1.0)


def run(config_path: str) -> int:
    cfg = load_config(config_path)
    rpc_url, volume_cfg, satellite_cfg = parse_configs(cfg)
    mopidy = MopidyClient(rpc_url=rpc_url, timeout=8.0)
    link = SerialLink()
    stop = threading.Event()

    def _stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    threads = [
        threading.Thread(
            target=audio_meter_loop,
            args=(stop, link, satellite_cfg),
            daemon=True,
        ),
        threading.Thread(
            target=serial_loop,
            args=(stop, link, mopidy, volume_cfg, satellite_cfg),
            daemon=True,
        ),
        threading.Thread(
            target=enforce_volume_cap,
            args=(mopidy, volume_cfg),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    log(f"satellite running; config={config_path}")

    while not stop.wait(1.0):
        pass
    link.send_line("X")
    log("satellite stopping")
    return 0


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG
    return run(config_path)


if __name__ == "__main__":
    raise SystemExit(main())
