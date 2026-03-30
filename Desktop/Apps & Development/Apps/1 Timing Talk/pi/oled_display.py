import io
import threading
import logging
import time
from collections import deque

import qrcode
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("timing-talk.oled")

SCREEN_WIDTH = 128
SCREEN_HEIGHT = 64
LIVE_DATA_LINES = 5
CHARS_PER_LINE = 21


def _load_font(size: int = 10):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", size)
    except (IOError, OSError):
        return ImageFont.load_default()


FONT_SM = _load_font(9)
FONT_MD = _load_font(11)
FONT_LG = _load_font(14)


class OLEDDisplay:
    def __init__(self, config: dict, get_status: callable):
        self.oled_config = config["oled"]
        self.network_config = config["network"]
        self.device_id = config.get("device_id", "unknown")[:8]
        self.get_status = get_status
        self._device = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._current_screen = 0
        self._screen_count = 3
        self._cycle_sec = self.oled_config.get("screen_cycle_sec", 5)
        self._force_screen: int | None = None
        self._data_buffer: deque[str] = deque(maxlen=LIVE_DATA_LINES)
        self._data_lock = threading.Lock()
        self._last_data_time: float = 0

    def _init_device(self):
        if not self.oled_config.get("enabled", True):
            logger.info("OLED disabled in config")
            return False
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306

            serial_interface = i2c(
                port=self.oled_config["i2c_port"],
                address=self.oled_config["i2c_address"],
            )
            self._device = ssd1306(
                serial_interface,
                width=SCREEN_WIDTH,
                height=SCREEN_HEIGHT,
                rotate=self.oled_config.get("rotation", 0),
            )
            logger.info("OLED initialized on I2C")
            return True
        except Exception as e:
            logger.warning("OLED init failed (running without display): %s", e)
            return False

    def _render(self, image: Image.Image):
        if self._device:
            self._device.display(image)

    def render_boot_screen(self):
        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 0)
        draw = ImageDraw.Draw(img)
        draw.text((10, 5), "TIMING TALK", font=FONT_LG, fill=1)
        draw.line([(5, 24), (123, 24)], fill=1)
        draw.text((10, 30), f"ID: {self.device_id}", font=FONT_SM, fill=1)
        draw.text((10, 42), "Starting...", font=FONT_SM, fill=1)
        self._render(img)

    def render_status_screen(self):
        status = self.get_status()
        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 0)
        draw = ImageDraw.Draw(img)

        serial_icon = ">" if status.get("serial_connected") else "X"
        tcp_clients = status.get("tcp_clients", 0)
        tcp_icon = str(tcp_clients) if tcp_clients > 0 else "X"
        cloud_icon = "^" if status.get("cloud_connected") else "X"

        draw.text((0, 0), f"SER:{serial_icon} TCP:{tcp_icon} NET:{cloud_icon}", font=FONT_SM, fill=1)
        draw.line([(0, 12), (127, 12)], fill=1)

        total = status.get("total_readings", 0)
        unsynced = status.get("unsynced", 0)
        draw.text((0, 16), f"Total: {total}", font=FONT_SM, fill=1)
        draw.text((0, 27), f"Queue: {unsynced}", font=FONT_SM, fill=1)

        tcp_port = status.get("tcp_port", 4000)
        draw.text((0, 38), f"TCP port: {tcp_port}", font=FONT_SM, fill=1)

        latest = status.get("latest_data", "")
        if latest:
            draw.text((0, 51), latest[:21], font=FONT_SM, fill=1)
        else:
            draw.text((0, 51), "No data yet", font=FONT_SM, fill=1)

        self._render(img)

    def render_qr_screen(self):
        ssid = f"{self.network_config['ap_ssid_prefix']}-{self.device_id}"
        password = self.network_config["ap_password"]
        wifi_string = f"WIFI:T:WPA;S:{ssid};P:{password};;"

        qr = qrcode.QRCode(version=1, box_size=2, border=1)
        qr.add_data(wifi_string)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="white", back_color="black").convert("1")

        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 0)
        draw = ImageDraw.Draw(img)

        qr_w, qr_h = qr_img.size
        qr_x = 0
        qr_y = (SCREEN_HEIGHT - qr_h) // 2
        img.paste(qr_img, (qr_x, qr_y))

        text_x = qr_w + 4
        draw.text((text_x, 5), "Scan to", font=FONT_SM, fill=1)
        draw.text((text_x, 16), "connect", font=FONT_SM, fill=1)
        draw.text((text_x, 32), ssid[:10], font=FONT_SM, fill=1)
        draw.text((text_x, 44), password[:10], font=FONT_SM, fill=1)

        self._render(img)

    def render_network_info_screen(self):
        status = self.get_status()
        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 0)
        draw = ImageDraw.Draw(img)

        draw.text((0, 0), "NETWORK", font=FONT_MD, fill=1)
        draw.line([(0, 14), (127, 14)], fill=1)

        ip = status.get("ip_address", "No IP")
        mode = status.get("network_mode", "unknown")
        draw.text((0, 18), f"Mode: {mode}", font=FONT_SM, fill=1)
        draw.text((0, 30), f"IP: {ip}", font=FONT_SM, fill=1)
        draw.text((0, 42), "Setup:", font=FONT_SM, fill=1)
        draw.text((0, 53), self._setup_target(status), font=FONT_SM, fill=1)

        self._render(img)

    def _setup_target(self, status: dict) -> str:
        ip = status.get("ip_address") or "No IP"
        if ip == "No IP":
            return "Waiting for IP"

        port = status.get("portal_port")
        if not port:
            if status.get("network_mode") == "ap":
                port = self.network_config.get("captive_portal_port", 80)
            else:
                port = self.network_config.get("portal_port_client", 8080)

        if port == 80:
            return ip[:CHARS_PER_LINE]
        return f"{ip}:{port}"[:CHARS_PER_LINE]

    def push_data(self, text: str):
        """Feed incoming serial data to the OLED live display."""
        with self._data_lock:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    self._data_buffer.append(stripped[:CHARS_PER_LINE])
            self._last_data_time = time.time()

    def render_live_data_screen(self):
        status = self.get_status()
        img = Image.new("1", (SCREEN_WIDTH, SCREEN_HEIGHT), 0)
        draw = ImageDraw.Draw(img)

        cloud_icon = "^" if status.get("cloud_connected") else "X"
        src = ""
        if status.get("serial_connected") and status.get("tcp_connected"):
            src = "SER+TCP"
        elif status.get("tcp_connected"):
            src = "TCP"
        elif status.get("serial_connected"):
            src = "SER"
        track = status.get("track_name", "")[:10] or self.device_id
        draw.text((0, 0), f"{track}", font=FONT_SM, fill=1)
        right_text = f"{src} {cloud_icon}"
        draw.text((128 - len(right_text) * 6, 0), right_text, font=FONT_SM, fill=1)
        draw.line([(0, 11), (127, 11)], fill=1)

        with self._data_lock:
            lines = list(self._data_buffer)

        if not lines:
            draw.text((10, 30), "Waiting for data...", font=FONT_SM, fill=1)
        else:
            y = 13
            for line in lines:
                draw.text((0, y), line, font=FONT_SM, fill=1)
                y += 10

        self._render(img)

    @property
    def _is_live(self) -> bool:
        """True when any data source is connected and WiFi is up."""
        status = self.get_status()
        has_data_source = (
            status.get("serial_connected", False)
            or status.get("tcp_connected", False)
        )
        has_network = (
            status.get("network_mode") != "ap"
            and status.get("ip_address", "") not in ("", "No IP")
        )
        return has_data_source and has_network

    def show_screen(self, screen_id: int):
        self._force_screen = screen_id

    def _display_loop(self):
        idle_screens = [
            self.render_status_screen,
            self.render_qr_screen,
            self.render_network_info_screen,
        ]
        self._screen_count = len(idle_screens)
        live_ticks = 0

        while self._running:
            try:
                if self._force_screen is not None:
                    idle_screens[self._force_screen % self._screen_count]()
                    self._force_screen = None
                    time.sleep(self._cycle_sec)
                    continue

                if self._is_live:
                    # Show live data most of the time, flash status briefly every 6th tick
                    live_ticks += 1
                    if live_ticks % 6 == 0:
                        self.render_status_screen()
                        time.sleep(2)
                    else:
                        self.render_live_data_screen()
                        time.sleep(1)
                else:
                    live_ticks = 0
                    idle_screens[self._current_screen % self._screen_count]()
                    self._current_screen = (self._current_screen + 1) % self._screen_count
                    time.sleep(self._cycle_sec)
            except Exception as e:
                logger.error("OLED render error: %s", e)
                time.sleep(self._cycle_sec)

    def start(self):
        if not self._init_device():
            logger.info("OLED running in headless mode (no physical display)")
        self._running = True
        self.render_boot_screen()
        time.sleep(2)
        self._thread = threading.Thread(target=self._display_loop, daemon=True, name="oled-display")
        self._thread.start()
        logger.info("OLED display loop started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._device:
            self._device.hide()
        logger.info("OLED display stopped")
