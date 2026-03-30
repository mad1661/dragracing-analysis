#!/usr/bin/env python3
"""
OLED display driver for the Mini ICE Tower's built-in SSD1306.
128x64 I2C OLED on address 0x3C (GPIO2/SDA, GPIO3/SCL).
"""

import threading
import time
import logging

log = logging.getLogger("oled")

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from luma.core.render import canvas
    from PIL import ImageFont
    HAS_OLED = True
except ImportError:
    HAS_OLED = False
    log.warning("luma.oled not installed — OLED display disabled")


# Scrollback buffer depth
MAX_LINES = 20


class OLEDDisplay:
    """
    Drives the SSD1306 OLED on the Mini ICE Tower.

    Three display modes:
      status  — 2-3 lines of status text (boot, wifi, etc.)
      data    — scrolling raw timing data
      split   — top line = status, rest = scrolling data
    """

    def __init__(self, port=1, address=0x3C):
        self._lock = threading.Lock()
        self._status_lines = []
        self._data_lines = []
        self._mode = "status"
        self._device = None

        if not HAS_OLED:
            return

        try:
            serial = i2c(port=port, address=address)
            self._device = ssd1306(serial, width=128, height=64)
            self._device.contrast(200)
            log.info("OLED initialized on I2C port %d addr 0x%02X", port, address)
        except Exception as e:
            log.error("Failed to initialize OLED: %s", e)
            self._device = None

    @property
    def available(self):
        return self._device is not None

    def _get_font(self, size=10):
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", size)
        except OSError:
            return ImageFont.load_default()

    def _render(self):
        if not self._device:
            return

        try:
            font = self._get_font(10)
            small_font = self._get_font(9)

            with canvas(self._device) as draw:
                if self._mode == "status":
                    for i, line in enumerate(self._status_lines[:5]):
                        draw.text((0, i * 13), line, fill="white", font=font)

                elif self._mode == "data":
                    visible = self._data_lines[-5:]
                    for i, line in enumerate(visible):
                        draw.text((0, i * 13), line[:21], fill="white", font=small_font)

                elif self._mode == "split":
                    if self._status_lines:
                        # Print IP on first line, Status on second line
                        if len(self._status_lines) >= 2:
                            draw.text((0, 0), self._status_lines[0][:21], fill="white", font=font)
                            draw.text((0, 13), self._status_lines[1][:21], fill="white", font=font)
                        else:
                            draw.text((0, 0), self._status_lines[0][:21], fill="white", font=font)
                            
                    draw.line([(0, 26), (128, 26)], fill="white")
                    
                    visible = self._data_lines[-3:]
                    for i, line in enumerate(visible):
                        draw.text((0, 29 + i * 12), line[:21], fill="white", font=small_font)
        except Exception as e:
            log.error("OLED render error: %s", e)

    def show_status(self, *lines):
        with self._lock:
            self._mode = "status"
            self._status_lines = list(lines)
            self._render()

    def show_data(self, line):
        import textwrap
        with self._lock:
            # Wrap long lines so they don't get cut off on the OLED
            wrapped = textwrap.wrap(line, width=21)
            if not wrapped:
                wrapped = [""]
            for w_line in wrapped:
                self._data_lines.append(w_line)
            
            if len(self._data_lines) > MAX_LINES:
                self._data_lines = self._data_lines[-MAX_LINES:]
            if self._mode != "split":
                self._mode = "data"
            self._render()

    def show_split(self, *status_lines):
        with self._lock:
            self._mode = "split"
            self._status_lines = list(status_lines)
            self._render()

    def clear(self):
        with self._lock:
            self._status_lines = []
            self._data_lines = []
            if self._device:
                self._device.clear()

    def off(self):
        if self._device:
            self._device.hide()
