"""Tiny Mopidy HTTP JSON-RPC client.

We talk to Mopidy over its built-in HTTP JSON-RPC interface (port 6680) rather
than MPD: it's bundled with Mopidy core (no extra extension), and a short-timeout
POST doubles as the "is Mopidy reachable yet?" probe that drives the controller's
startup/LED states later. Standard library only (urllib) — keep it light for the
Pi Zero 2 W.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class MopidyError(Exception):
    pass


class MopidyClient:
    def __init__(self, rpc_url: str = "http://127.0.0.1:6680/mopidy/rpc", timeout: float = 5.0):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, **params):
        """Make one JSON-RPC call. Raises MopidyError on transport/RPC error."""
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params:
            payload["params"] = params
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.rpc_url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise MopidyError(f"{method} transport error: {e}") from e
        if "error" in body:
            raise MopidyError(f"{method} RPC error: {body['error']}")
        return body.get("result")

    def is_ready(self) -> bool:
        try:
            self.call("core.get_version")
            return True
        except MopidyError:
            return False

    def wait_until_ready(self, timeout: float = 120.0, interval: float = 2.0, log=print) -> bool:
        """Poll until Mopidy answers or timeout elapses. Returns True if ready."""
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            if self.is_ready():
                return True
            if log and attempt % 5 == 1:
                log(f"waiting for Mopidy… (attempt {attempt})")
            time.sleep(interval)
        return False

    # --- convenience playback helpers ---
    def get_state(self):
        return self.call("core.playback.get_state")

    def get_current_track(self):
        return self.call("core.playback.get_current_track")

    # --- convenience mixer helpers ---
    def get_volume(self):
        """Return Mopidy software-mixer volume (0..100), or None if unknown."""
        return self.call("core.mixer.get_volume")

    def set_volume(self, volume: int) -> int:
        """Clamp and set Mopidy software-mixer volume. Returns the requested value."""
        volume = max(0, min(100, int(volume)))
        self.call("core.mixer.set_volume", volume=volume)
        return volume

    def nudge_volume(self, delta: int, lo: int = 0, hi: int = 100) -> int:
        """Move volume by delta within [lo, hi]. Unknown current volume starts at 50."""
        lo = max(0, min(100, int(lo)))
        hi = max(lo, min(100, int(hi)))
        cur = self.get_volume()
        if cur is None:
            cur = 50
        return self.set_volume(max(lo, min(hi, int(cur) + int(delta))))
