import glob
import threading
import logging
import time
from typing import Callable

import serial

logger = logging.getLogger("timing-talk.serial")


class SerialReader:
    def __init__(self, config: dict):
        self.serial_config = config["serial"]
        self._port: serial.Serial | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable[[bytes, str], None]] = []
        self._current_port_name = ""

    def on_data(self, callback: Callable[[bytes, str], None]):
        """Register a callback: callback(raw_bytes, port_name)"""
        self._callbacks.append(callback)

    def _find_serial_port(self) -> str | None:
        configured = self.serial_config["port"]
        if configured != "auto":
            return configured

        patterns = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"]
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                logger.info("Auto-detected serial port: %s", matches[0])
                return matches[0]
        return None

    def _open_port(self) -> serial.Serial | None:
        port_name = self._find_serial_port()
        if not port_name:
            return None

        try:
            s = self.serial_config
            port = serial.Serial(
                port=port_name,
                baudrate=s["baud_rate"],
                bytesize=s["byte_size"],
                parity=s["parity"],
                stopbits=s["stop_bits"],
                timeout=s["timeout"],
            )
            self._current_port_name = port_name
            logger.info("Opened %s at %d baud", port_name, s["baud_rate"])
            return port
        except serial.SerialException as e:
            logger.error("Failed to open %s: %s", port_name, e)
            return None

    def _read_loop(self):
        while self._running:
            if self._port is None or not self._port.is_open:
                self._port = self._open_port()
                if self._port is None:
                    logger.debug("No serial port found, retrying in 3s...")
                    time.sleep(3)
                    continue

            try:
                line = self._port.readline()
                if line:
                    self._emit(line)
            except serial.SerialException as e:
                logger.warning("Serial read error: %s", e)
                self._safe_close()
                time.sleep(2)
            except Exception as e:
                logger.exception("Unexpected serial error: %s", e)
                self._safe_close()
                time.sleep(2)

    def _safe_close(self):
        if self._port:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    def _emit(self, data: bytes):
        for cb in self._callbacks:
            try:
                cb(data, self._current_port_name)
            except Exception as e:
                logger.error("Callback error: %s", e)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="serial-reader")
        self._thread.start()
        logger.info("Serial reader started")

    def stop(self):
        self._running = False
        self._safe_close()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Serial reader stopped")

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def port_name(self) -> str:
        return self._current_port_name if self.is_connected else ""
