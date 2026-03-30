"""Generates realistic fake drag racing timing data for testing."""

import threading
import logging
import time
import random
import json

logger = logging.getLogger("timing-talk.testgen")

CLASSES = [
    ("TF", "Top Fuel", 3.6, 4.1, 310, 340),
    ("FC", "Funny Car", 3.7, 4.2, 305, 335),
    ("PS", "Pro Stock", 6.4, 6.7, 208, 215),
    ("PSM", "Pro Stock Motorcycle", 6.7, 7.0, 195, 205),
    ("TD", "Top Dragster", 6.0, 6.6, 210, 230),
    ("TA", "Top Alcohol", 5.1, 5.6, 270, 285),
    ("SS", "Super Stock", 8.5, 11.5, 120, 155),
    ("STK", "Stock", 9.5, 13.0, 100, 140),
    ("SC", "Super Comp", 8.8, 8.92, 155, 175),
    ("SG", "Super Gas", 9.88, 9.92, 135, 150),
    ("COMP", "Comp", 6.5, 9.0, 140, 210),
    ("JR", "Jr Dragster", 7.9, 13.5, 45, 85),
]


def _random_rt():
    if random.random() < 0.05:
        return -abs(round(random.uniform(0.001, 0.050), 3))
    return round(random.uniform(0.010, 0.700), 3)


def _random_run():
    cls = random.choice(CLASSES)
    et = round(random.uniform(cls[2], cls[3]), 3)
    mph = round(random.uniform(cls[4], cls[5]), 2)
    rt = _random_rt()
    lane = random.choice(["L", "R"])
    return {
        "class_code": cls[0],
        "class_name": cls[1],
        "et": et,
        "mph": mph,
        "rt": rt,
        "lane": lane,
        "foul": rt < 0,
    }


def generate_serial_line() -> bytes:
    """Generate a fake serial data line that looks like real timing system output."""
    run = _random_run()
    foul_flag = " FOUL" if run["foul"] else ""
    line = (
        f'{run["lane"]}  '
        f'ET {run["et"]:7.3f}  '
        f'MPH {run["mph"]:7.2f}  '
        f'RT {abs(run["rt"]):.3f}'
        f'{foul_flag}  '
        f'{run["class_code"]}'
    )
    return (line + "\r\n").encode("ascii")


def generate_tcp_json() -> dict:
    """Generate a fake structured JSON payload like a TCP timing feed."""
    run = _random_run()
    return {
        "lane": run["lane"],
        "et": str(run["et"]),
        "mph": str(run["mph"]),
        "rt": str(run["rt"]),
        "class": run["class_name"],
        "classCode": run["class_code"],
        "foul": run["foul"],
        "round": random.randint(1, 6),
        "pair": random.randint(1, 16),
    }


class TestDataGenerator:
    """Injects fake timing data into the same callbacks as real serial/TCP sources."""

    def __init__(self, config: dict):
        self._interval = config.get("test", {}).get("interval_sec", 3)
        self._serial_enabled = config.get("test", {}).get("serial", True)
        self._tcp_enabled = config.get("test", {}).get("tcp", True)
        self._running = False
        self._thread = None
        self._serial_callbacks = []
        self._tcp_data_callbacks = []
        self._tcp_json_callbacks = []

    def on_serial_data(self, callback):
        self._serial_callbacks.append(callback)

    def on_tcp_data(self, callback):
        self._tcp_data_callbacks.append(callback)

    def on_tcp_json(self, callback):
        self._tcp_json_callbacks.append(callback)

    def _emit_serial(self, data: bytes):
        for cb in self._serial_callbacks:
            try:
                cb(data, "test:serial")
            except Exception as e:
                logger.error("Serial callback error: %s", e)

    def _emit_tcp(self, data: bytes, parsed: dict):
        for cb in self._tcp_data_callbacks:
            try:
                cb(data, "tcp:test")
            except Exception as e:
                logger.error("TCP data callback error: %s", e)
        for cb in self._tcp_json_callbacks:
            try:
                cb(parsed, "tcp:test")
            except Exception as e:
                logger.error("TCP JSON callback error: %s", e)

    def _run_loop(self):
        pair = 0
        while self._running:
            pair += 1
            if pair > 16:
                pair = 1

            if self._serial_enabled:
                line = generate_serial_line()
                logger.info("TEST serial: %s", line.decode().strip())
                self._emit_serial(line)

            if self._tcp_enabled:
                jdata = generate_tcp_json()
                jdata["pair"] = pair
                raw = json.dumps(jdata).encode("utf-8")
                logger.info("TEST tcp: %s", jdata)
                self._emit_tcp(raw, jdata)

            delay = self._interval + random.uniform(-0.5, 1.0)
            time.sleep(max(0.5, delay))

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="test-data-gen"
        )
        self._thread.start()
        logger.info(
            "Test data generator started (interval=%ds, serial=%s, tcp=%s)",
            self._interval, self._serial_enabled, self._tcp_enabled,
        )

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Test data generator stopped")

    @property
    def is_running(self) -> bool:
        return self._running
