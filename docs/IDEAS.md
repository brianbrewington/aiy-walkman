# Walkman — feature backlog / future ideas

Captured ideas beyond the core build (Steps 0–5). Not committed to a timeline.

## Requested by the family (2026-06-06 — first battery demo, family approved! 😄)

- **Repeat-track ("play this song again")** — *requested by Nathan (12).*
  Good news: this is **built into Mopidy core** — `core.tracklist.set_single(true)`
  + `set_repeat(true)` repeats the current track. So it does NOT necessarily need the
  CPX. Options for how to trigger it:
  - a button gesture on the main button (but the single button is already full:
    single=play/pause, double=next, long=shutdown — would need a triple-press or a
    press-and-hold variant), or
  - a control on the future CPX satellite, or
  - default off; toggled via the satellite/secondary input.
  Likely lands as a **CPX control** to avoid overloading the one button.

- **Now-playing LCD screen ("Phase 3")** — a small LCD/OLED showing current track
  title/artist. Fits the **secondary status display** role already planned for the
  CPX satellite (driven over the same USB-serial link — the Pi already knows the
  track via Mopidy's JSON-RPC `core.playback.get_current_track`). An I2C OLED
  directly on the bonnet's spare I2C is also possible, but the bonnet's headers are
  occupied; the CPX/serial route keeps the main unit's single-cable simplicity.
  See the satellite-expansion hook in `PLAN.md`.

## Notes
- These reinforce the value of the **pluggable input layer + bidirectional CPX
  serial channel** already in the design: volume, repeat-track, and a now-playing
  display can all attach there without touching the core controller.
