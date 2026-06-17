"""Circuit Playground Express boot config for the Walkman satellite.

Keep the normal CircuitPython console and expose a second USB CDC data channel for
the Pi service. Copy this file to CIRCUITPY/boot.py, then reset the CPX.
"""
import usb_cdc

usb_cdc.enable(console=True, data=True)
