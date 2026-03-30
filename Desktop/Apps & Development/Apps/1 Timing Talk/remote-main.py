#!/usr/bin/env python3
"""
NHRA Timing Pi — Main entry point.

Boot sequence:
  1. Init OLED display
  2. Check WiFi connection
  3. If connected → start web dashboard + timing sender
  4. If not connected → start hotspot + captive portal
  5. Once WiFi connects (via portal) → stop hotspot, start sender

The web interface is always running:
  Setup mode  → http://10.0.0.1        (captive portal for WiFi config)
  Online mode → http://nhra-timing.local (dashboard, live data, WiFi settings)
"""

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
import time
import signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("main")

from oled_display import OLEDDisplay
import wifi_manager
import captive_portal
from sender import TimingSender

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 9600


def _connect_screen_sharing_active():
    """Return True when Raspberry Connect has an active screen-sharing session."""
    try:
        result = subprocess.run(
            [
                "sudo", "-u", "pi", "env",
                "XDG_RUNTIME_DIR=/run/user/1000",
                "rpi-connect", "status",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = result.stdout or ""
        for line in output.splitlines():
            if line.startswith("Screen sharing:") and "sessions active" in line:
                count = line.rsplit("(", 1)[-1].split(" ", 1)[0]
                return count.isdigit() and int(count) > 0
    except Exception as exc:
        log.debug("Could not read Raspberry Connect status: %s", exc)
    return False


def _start_connect_desktop_monitor():
    """If Raspberry Connect opens a screen-sharing session, drop out of kiosk to desktop."""
    def _worker():
        saw_active_session = False
        stop_script = "/home/pi/timing-sender/stop-kiosk.sh"
        while True:
            active = _connect_screen_sharing_active()
            if active and not saw_active_session:
                try:
                    subprocess.run([stop_script], check=False, timeout=15)
                    log.info("Raspberry Connect session detected; switched from kiosk to desktop")
                except Exception as exc:
                    log.warning("Could not switch kiosk to desktop for Raspberry Connect: %s", exc)
            saw_active_session = active
            time.sleep(5)

    threading.Thread(target=_worker, daemon=True).start()


def _wait_for_ip(timeout=15, interval=1):
    deadline = time.time() + timeout
    last_ip = wifi_manager.get_ip()
    while time.time() < deadline:
        if last_ip:
            return last_ip
        time.sleep(interval)
        last_ip = wifi_manager.get_ip()
    return last_ip


def _switch_to_setup_mode(oled):
    log.warning("Network lost; switching to setup mode")

    try:
        subprocess.run(["/home/pi/timing-sender/stop-kiosk.sh"], check=False, timeout=15)
    except Exception as exc:
        log.warning("Could not stop kiosk during network recovery: %s", exc)

    wifi_manager.start_hotspot()
    captive_portal.set_mode("setup")
    captive_portal.update_status(wifi_ssid=wifi_manager.HOTSPOT_SSID, wifi_ip=wifi_manager.AP_IP)

    if oled:
        try:
            oled.show_status(
                "WiFi Setup Mode",
                f"SSID: {wifi_manager.HOTSPOT_SSID}",
                f"PW: {wifi_manager.HOTSPOT_PASS}",
                "Open in browser:",
                f"http://{wifi_manager.AP_IP}",
            )
        except Exception:
            pass


def _switch_to_online_mode(oled, args):
    log.info("Network detected; switching to online mode")
    wifi_manager.stop_hotspot()
    ip = _wait_for_ip()
    network_name = wifi_manager.get_ssid() or "Network"
    ts_ip = wifi_manager.get_tailscale_ip()
    display_ip = ip or "Waiting..."

    captive_portal.set_mode("online")
    captive_portal.update_status(wifi_ssid=network_name, wifi_ip=ip, tailscale_ip=ts_ip)

    if oled:
        try:
            oled.show_status("NHRA TIMING", f"Net: {network_name}", f"IP: {display_ip}", "Starting...")
            time.sleep(2)
            oled.show_split(f"IP: {display_ip}", f"Net: {network_name}")
        except Exception:
            pass

    if _sender is None:
        start_sender(oled, args)

    maybe_launch_kiosk()


def _start_network_monitor(oled, args):
    """Switch modes whenever real network connectivity changes."""
    def _worker():
        had_network = wifi_manager.is_connected()

        while True:
            time.sleep(5)
            connected = wifi_manager.is_connected()

            if connected and not had_network:
                _switch_to_online_mode(oled, args)
            elif not connected and had_network:
                _switch_to_setup_mode(oled)

            had_network = connected

    threading.Thread(target=_worker, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="NHRA Timing Pi")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Serial port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate")
    parser.add_argument("--no-oled", action="store_true", help="Disable OLED display")
    args = parser.parse_args()

    # ── Init OLED ──
    oled = OLEDDisplay() if not args.no_oled else None
    if oled and oled.available:
        oled.show_status("NHRA TIMING", "Starting...")
    elif oled and not oled.available:
        oled = None
        log.warning("OLED not available, running without display")

    captive_portal.load_event_config()
    device_cfg = captive_portal.load_device_config()
    if device_cfg.get("port"):
        args.port = device_cfg["port"]
        log.info("Loaded saved serial port: %s", args.port)
    if device_cfg.get("baud"):
        args.baud = device_cfg["baud"]
        log.info("Loaded saved baud rate: %d", args.baud)
    
    args.tcp_host = device_cfg.get("tcp_host")
    args.tcp_port = device_cfg.get("tcp_port")
    if args.tcp_host and args.tcp_port:
        log.info("Loaded saved TCP config: %s:%s", args.tcp_host, args.tcp_port)
    
    args.input_mode = device_cfg.get("input_mode", "both")
    log.info("Loaded input mode: %s", args.input_mode)

    args.secondary_url = device_cfg.get("secondary_url")
    if args.secondary_url:
        log.info("Loaded secondary API URL: %s", args.secondary_url)

    time.sleep(1)

    # ── Check WiFi ──
    log.info("Checking WiFi connection...")
    if oled:
        oled.show_status("NHRA TIMING", "Checking WiFi...")

    if wifi_manager.is_connected():
        start_online_mode(oled, args)
    else:
        start_setup_mode(oled, args)

    _start_network_monitor(oled, args)

    # Keep main thread alive
    try:
        signal.pause()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        if oled:
            oled.show_status("Shutting down...")
            time.sleep(1)
            oled.off()


_sender = None


def start_sender(oled, args):
    """Start the timing sender with OLED and web feed hooks."""
    global _sender
    ip = wifi_manager.get_ip()

    _sender = TimingSender(
        port=args.port, 
        baud=args.baud,
        tcp_host=getattr(args, 'tcp_host', None),
        tcp_port=getattr(args, 'tcp_port', None),
        input_mode=getattr(args, 'input_mode', 'both'),
        secondary_url=getattr(args, 'secondary_url', None)
    )
    _sender.set_event_config_getter(captive_portal.get_event_config)

    captive_portal.update_status(
        serial_port=args.port,
        serial_status="Connecting..." if args.input_mode in ["serial", "both"] else "Disabled",
        tcp_status="Connecting..." if (getattr(args, 'tcp_host', None) and args.input_mode in ["tcp", "both"]) else "Disabled",
        input_mode=args.input_mode,
        baud_rate=args.baud,
        api_url=_sender.api_url,
    )

    def on_data(line):
        captive_portal.push_data_line(line)
        if oled:
            oled.show_data(line)

    def on_status(msg, is_tcp=False):
        if is_tcp:
            captive_portal.update_status(tcp_status=msg)
        else:
            captive_portal.update_status(serial_status=msg)
            
        if oled:
            if args.input_mode == "both":
                s_stat = _sender._serial_status[:8]
                t_stat = _sender._tcp_status[:8]
                oled_msg = f"S:{s_stat} T:{t_stat}"
            elif args.input_mode == "tcp":
                oled_msg = f"TCP: {msg}"
            else:
                oled_msg = f"SER: {msg}"
            oled.show_split(f"IP: {ip}", oled_msg)

    _sender.on_data(on_data)
    _sender.on_status(on_status)
    _sender.start()

    def handle_port_change(new_port, new_baud=None):
        if _sender:
            _sender.change_config(port=new_port, baud=new_baud)
            captive_portal.update_status(
                serial_port=new_port,
                serial_status="Switching...",
            )
            if new_baud is not None:
                captive_portal.update_status(baud_rate=new_baud)
                args.baud = new_baud
            args.port = new_port

    captive_portal.set_on_port_change(handle_port_change)

    def handle_tcp_change(new_host, new_port):
        if _sender:
            _sender.change_config(tcp_host=new_host, tcp_port=new_port)
            captive_portal.update_status(
                tcp_status="Switching..." if new_host else "Disabled",
            )
            args.tcp_host = new_host
            args.tcp_port = new_port
            
    captive_portal.set_on_tcp_change(handle_tcp_change)

    def handle_mode_change(new_mode):
        if _sender:
            _sender.change_config(input_mode=new_mode)
            args.input_mode = new_mode

    captive_portal.set_on_mode_change(handle_mode_change)

    def handle_secondary_url_change(new_url):
        if _sender:
            _sender.change_config(secondary_url=new_url)
            args.secondary_url = new_url

    captive_portal.set_on_secondary_url_change(handle_secondary_url_change)

    def handle_test_serial():
        from sender import TEST_RACE_DATA, send_or_queue, DEFAULT_API
        if _sender:
            _sender.inject_test_data(TEST_RACE_DATA, delay=0.3)
        batch = "\n".join(TEST_RACE_DATA)
        device_id = _sender.device_id if _sender else None
        secondary_url = _sender.secondary_url if _sender else getattr(args, 'secondary_url', None)
        threading.Thread(
            target=send_or_queue,
            args=(_sender.api_url if _sender else DEFAULT_API, batch, device_id, secondary_url),
            daemon=True,
        ).start()

    captive_portal.set_on_test_serial(handle_test_serial)
    log.info("Timing sender started on %s @ %d baud", args.port, args.baud)


def start_online_mode(oled, args):
    """Network is connected — start web dashboard + timing sender."""
    wifi_manager.stop_hotspot()
    ip = _wait_for_ip()
    network_name = wifi_manager.get_ssid() or "Network"
    display_ip = ip or "Waiting..."
    log.info("Connected to '%s' at %s", network_name, ip)

    if oled:
        oled.show_status("NHRA TIMING", f"Net: {network_name}", f"IP: {display_ip}", "Starting...")
    time.sleep(2)

    # Switch web interface to dashboard mode
    captive_portal.set_mode("online")
    ts_ip = wifi_manager.get_tailscale_ip()
    captive_portal.update_status(wifi_ssid=network_name, wifi_ip=ip, tailscale_ip=ts_ip)
    captive_portal.start_background(port=80)
    log.info("Web dashboard running at http://%s", ip)

    if oled:
        oled.show_split(f"IP: {display_ip}", f"Net: {network_name}")

    if _sender is None:
        start_sender(oled, args)

    maybe_launch_kiosk()


def maybe_launch_kiosk():
    if not captive_portal.get_device_config().get("kiosk_autostart"):
        return

    try:
        import urllib.request
        for _ in range(30):
            try:
                urllib.request.urlopen("http://127.0.0.1/kiosk", timeout=1)
                break
            except Exception:
                time.sleep(1)

        subprocess.run(["sudo", "-u", "pi", "/home/pi/timing-sender/start-kiosk.sh"], check=False, timeout=15)
        log.info("Launched Chromium in kiosk mode")
    except Exception as e:
        log.warning("Could not launch kiosk browser: %s", e)


def start_setup_mode(oled, args):
    """No WiFi — start hotspot and captive portal."""
    log.info("No WiFi connection. Starting setup mode...")

    if oled:
        oled.show_status(
            "NHRA TIMING",
            "No WiFi found",
            "",
            "Connect to WiFi:",
            f"  {wifi_manager.HOTSPOT_SSID}",
        )

    wifi_manager.start_hotspot()

    try:
        subprocess.run(["/home/pi/timing-sender/stop-kiosk.sh"], check=False, timeout=15)
        log.info("Desktop shell restored for setup mode")
    except Exception as e:
        log.warning("Could not restore desktop shell in setup mode: %s", e)

    if oled:
        oled.show_status(
            "WiFi Setup Mode",
            f"SSID: {wifi_manager.HOTSPOT_SSID}",
            f"PW: {wifi_manager.HOTSPOT_PASS}",
            "Open in browser:",
            f"http://{wifi_manager.AP_IP}",
        )

    def on_wifi_connected():
        """Called by the captive portal when WiFi connects."""
        time.sleep(3)
        ip = _wait_for_ip()
        ssid = wifi_manager.get_ssid()
        display_ip = ip or "Waiting..."
        log.info("WiFi connected via portal to '%s' at %s", ssid, ip)

        captive_portal.set_mode("online")
        ts_ip = wifi_manager.get_tailscale_ip()
        captive_portal.update_status(wifi_ssid=ssid, wifi_ip=ip, tailscale_ip=ts_ip)

        if oled:
            oled.show_status("NHRA TIMING", f"WiFi: {ssid}", f"IP: {display_ip}", "Starting sender...")
        time.sleep(2)

        if oled:
            oled.show_split(f"IP: {display_ip}", f"Net: {ssid}")

        if _sender is None:
            start_sender(oled, args)
        maybe_launch_kiosk()

    captive_portal.set_on_connected(on_wifi_connected)
    captive_portal.start_background(port=80)

    log.info("Captive portal running. Waiting for WiFi setup...")


if __name__ == "__main__":
    main()
