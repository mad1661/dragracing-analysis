#!/usr/bin/env python3
"""Timing Talk - Pi Edge Device Orchestrator"""

import argparse
import signal
import sys
import logging
import threading

from config import load_config, save_config, ensure_dirs
from serial_reader import SerialReader
from tcp_receiver import TCPReceiver
from test_data_generator import TestDataGenerator
from data_logger import DataLogger
from cloud_sync import CloudSync
from oled_display import OLEDDisplay
from network_manager import NetworkManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/log/timing-talk.log", mode="a"),
    ],
)
logger = logging.getLogger("timing-talk")

_shutdown_event = threading.Event()


def main():
    parser = argparse.ArgumentParser(description="Timing Talk - Pi Edge Device")
    parser.add_argument(
        "--test", action="store_true",
        help="Enable test mode: generate fake timing data (serial + TCP)",
    )
    parser.add_argument(
        "--test-serial", action="store_true",
        help="Test mode: generate only fake serial data",
    )
    parser.add_argument(
        "--test-tcp", action="store_true",
        help="Test mode: generate only fake TCP data",
    )
    parser.add_argument(
        "--test-interval", type=float, default=None,
        help="Seconds between test data bursts (default: 3)",
    )
    args = parser.parse_args()

    logger.info("=== Timing Talk starting ===")

    config = load_config()
    ensure_dirs(config)

    test_mode = args.test or args.test_serial or args.test_tcp or config["test"]["enabled"]
    if test_mode:
        if args.test_serial:
            config["test"]["serial"] = True
            config["test"]["tcp"] = False
        elif args.test_tcp:
            config["test"]["serial"] = False
            config["test"]["tcp"] = True
        else:
            config["test"]["serial"] = True
            config["test"]["tcp"] = True
        config["test"]["enabled"] = True
        if args.test_interval is not None:
            config["test"]["interval_sec"] = args.test_interval

    save_config(config)

    logger.info("Device ID: %s", config["device_id"][:8])
    logger.info("Track: %s", config.get("track_name", "Not set"))
    if test_mode:
        logger.info("*** TEST MODE ACTIVE (serial=%s, tcp=%s, interval=%ss) ***",
                     config["test"]["serial"], config["test"]["tcp"],
                     config["test"]["interval_sec"])

    data_logger = DataLogger(config)
    serial_reader = SerialReader(config)
    tcp_receiver = TCPReceiver(config)
    test_gen = TestDataGenerator(config) if test_mode else None
    cloud_sync = CloudSync(config, data_logger)
    network_mgr = NetworkManager(config, save_config_fn=save_config)
    portal_state = {"port": None}

    def get_system_status() -> dict:
        db_stats = data_logger.get_stats()
        status = {
            "serial_connected": serial_reader.is_connected,
            "serial_port": serial_reader.port_name,
            "tcp_connected": tcp_receiver.is_connected,
            "tcp_clients": tcp_receiver.client_count,
            "tcp_port": tcp_receiver.port,
            "cloud_connected": cloud_sync.is_connected,
            "network_mode": network_mgr.mode,
            "ip_address": network_mgr.ip_address,
            "connected_ssid": network_mgr.connected_ssid,
            "track_name": config.get("track_name", ""),
            "event_name": config.get("event_name", ""),
            "portal_port": portal_state["port"],
            "test_mode": test_mode,
            **db_stats,
        }
        return status

    oled = OLEDDisplay(config, get_system_status)

    def on_serial_data(raw_bytes: bytes, port_name: str):
        data_logger.log(raw_bytes, port_name)
        raw_text = raw_bytes.decode("ascii", errors="replace").strip()
        if raw_text:
            cloud_sync.push_live(raw_text, port_name)
            oled.push_data(raw_text)

    def on_tcp_data(raw_bytes: bytes, source: str):
        data_logger.log(raw_bytes, source)
        raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
        if raw_text:
            cloud_sync.push_live(raw_text, source)
            oled.push_data(raw_text)

    def on_tcp_json(parsed: dict, source: str):
        cloud_sync.push_live_json(parsed, source)

    serial_reader.on_data(on_serial_data)
    tcp_receiver.on_data(on_tcp_data)
    tcp_receiver.on_json(on_tcp_json)

    if test_gen:
        test_gen.on_serial_data(on_serial_data)
        test_gen.on_tcp_data(on_tcp_data)
        test_gen.on_tcp_json(on_tcp_json)

    network_mgr.start()
    portal_state["port"] = _start_portal(network_mgr, config)
    oled.start()
    serial_reader.start()
    tcp_receiver.start()
    cloud_sync.start()

    if test_gen:
        test_gen.start()

    def shutdown(signum=None, frame=None):
        logger.info("Shutting down...")
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("=== Timing Talk running ===")
    _shutdown_event.wait()

    if test_gen:
        test_gen.stop()
    serial_reader.stop()
    tcp_receiver.stop()
    cloud_sync.stop()
    oled.stop()
    network_mgr.stop()
    logger.info("=== Timing Talk stopped ===")


def _start_portal(network_mgr, config):
    from captive_portal.app import create_app

    portal_app = create_app(network_mgr, config=config, save_config_fn=save_config)

    if network_mgr.mode == "ap":
        port = config["network"].get("captive_portal_port", 80)
    else:
        port = config["network"].get("portal_port_client", 8080)

    portal_thread = threading.Thread(
        target=lambda: portal_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
        name="web-portal",
    )
    portal_thread.start()
    logger.info("Web portal running on http://%s:%d", network_mgr.ip_address, port)
    return port


if __name__ == "__main__":
    main()
