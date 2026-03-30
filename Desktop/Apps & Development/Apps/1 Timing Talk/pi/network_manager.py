import subprocess
import logging
import time
import threading
import socket

logger = logging.getLogger("timing-talk.network")


class NetworkManager:
    def __init__(self, config: dict, save_config_fn=None):
        self.config = config["network"]
        self.full_config = config
        self._save_config_fn = save_config_fn
        self.device_id = config.get("device_id", "unknown")[:8]
        self.ap_ssid = f"{self.config['ap_ssid_prefix']}-{self.device_id}"
        self.ap_password = self.config["ap_password"]
        self._mode = "unknown"
        self._ip_address = ""
        self._connected_ssid = ""
        self._running = False
        self._monitor_thread: threading.Thread | None = None

    # ── Saved WiFi management ───────────────────────────────────────────────

    def _get_saved_wifi(self) -> list[dict]:
        return self.config.get("saved_wifi", [])

    def _save_wifi(self, ssid: str, password: str):
        """Remember a WiFi network. Updates existing entry or adds new one."""
        saved = self._get_saved_wifi()
        for entry in saved:
            if entry["ssid"] == ssid:
                entry["password"] = password
                self._persist_saved_wifi(saved)
                return
        saved.append({"ssid": ssid, "password": password})
        self._persist_saved_wifi(saved)

    def _forget_wifi(self, ssid: str):
        saved = [w for w in self._get_saved_wifi() if w["ssid"] != ssid]
        self._persist_saved_wifi(saved)
        self._run_cmd(["nmcli", "connection", "delete", ssid])
        logger.info("Forgot WiFi: %s", ssid)

    def _persist_saved_wifi(self, wifi_list: list[dict]):
        self.config["saved_wifi"] = wifi_list
        self.full_config["network"] = self.config
        if self._save_config_fn:
            self._save_config_fn(self.full_config)

    def get_saved_networks(self) -> list[str]:
        return [w["ssid"] for w in self._get_saved_wifi()]

    def forget_network(self, ssid: str):
        self._forget_wifi(ssid)

    # ── Low-level helpers ───────────────────────────────────────────────────

    def _run_cmd(self, cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=check)
        except subprocess.TimeoutExpired:
            logger.warning("Command timed out: %s", " ".join(cmd))
            return subprocess.CompletedProcess(cmd, 1, "", "timeout")
        except subprocess.CalledProcessError as e:
            logger.warning("Command failed: %s -> %s", " ".join(cmd), e.stderr)
            return subprocess.CompletedProcess(cmd, e.returncode, e.stdout, e.stderr)

    def get_ip_address(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return ""

    def has_internet(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except Exception:
            return False

    def _get_active_ssid(self) -> str:
        result = self._run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"])
        if result.returncode != 0:
            return ""
        for line in result.stdout.strip().split("\n"):
            if line.startswith("yes:"):
                return line.split(":", 1)[1]
        return ""

    # ── WiFi operations ─────────────────────────────────────────────────────

    def connect_wifi(self, ssid: str, password: str) -> bool:
        logger.info("Connecting to WiFi: %s", ssid)

        if self._mode == "ap":
            self.stop_ap()
            time.sleep(2)

        result = self._run_cmd([
            "nmcli", "device", "wifi", "connect", ssid, "password", password,
        ])
        if result.returncode == 0:
            logger.info("Connected to WiFi: %s", ssid)
            self._save_wifi(ssid, password)
            self._mode = "client"
            self._connected_ssid = ssid
            self._ip_address = self.get_ip_address()
            return True

        logger.error("WiFi connection failed: %s", result.stderr)
        return False

    def _try_saved_networks(self) -> bool:
        """Cycle through all remembered WiFi networks and connect to the first available."""
        saved = self._get_saved_wifi()
        if not saved:
            return False

        available = {n["ssid"] for n in self.scan_wifi()}

        for entry in saved:
            if entry["ssid"] in available:
                logger.info("Found known network: %s", entry["ssid"])
                if self.connect_wifi(entry["ssid"], entry["password"]):
                    return True

        # Also try nmcli's own saved connections (for networks saved outside our config)
        self._run_cmd(["nmcli", "networking", "on"])
        time.sleep(5)
        if self.has_internet():
            self._ip_address = self.get_ip_address()
            self._connected_ssid = self._get_active_ssid()
            self._mode = "client"
            logger.info("Connected via nmcli saved connection: %s", self._connected_ssid)
            return True

        return False

    def scan_wifi(self) -> list[dict]:
        self._run_cmd(["nmcli", "device", "wifi", "rescan"])
        time.sleep(2)
        result = self._run_cmd(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
        if result.returncode != 0:
            return []
        networks = []
        seen = set()
        saved_ssids = set(self.get_saved_networks())
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append({
                    "ssid": parts[0],
                    "signal": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2],
                    "saved": parts[0] in saved_ssids,
                })
        return sorted(networks, key=lambda n: n["signal"], reverse=True)

    # ── AP mode ─────────────────────────────────────────────────────────────

    def start_ap(self) -> bool:
        logger.info("Starting WiFi AP: %s", self.ap_ssid)

        self._run_cmd(["nmcli", "connection", "delete", self.ap_ssid])

        result = self._run_cmd([
            "nmcli", "connection", "add",
            "type", "wifi",
            "ifname", "wlan0",
            "con-name", self.ap_ssid,
            "autoconnect", "yes",
            "ssid", self.ap_ssid,
            "mode", "ap",
            "ipv4.method", "shared",
            "ipv4.addresses", "192.168.4.1/24",
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", self.ap_password,
        ])

        if result.returncode == 0:
            self._run_cmd(["nmcli", "connection", "up", self.ap_ssid])
            self._mode = "ap"
            self._ip_address = "192.168.4.1"
            logger.info("AP started at 192.168.4.1")
            return True

        logger.error("AP start failed: %s", result.stderr)
        return False

    def stop_ap(self):
        self._run_cmd(["nmcli", "connection", "down", self.ap_ssid])
        self._run_cmd(["nmcli", "connection", "delete", self.ap_ssid])
        logger.info("AP stopped")

    # ── Boot & monitoring ───────────────────────────────────────────────────

    def setup_network(self):
        """Main network setup logic. Called on boot."""
        # Already connected (ethernet or previously configured WiFi)?
        ip = self.get_ip_address()
        if ip:
            self._ip_address = ip
            self._connected_ssid = self._get_active_ssid()
            if self.has_internet():
                self._mode = "client"
                logger.info("Already connected: %s (SSID: %s)", ip, self._connected_ssid)
                return
            else:
                self._mode = "ethernet"
                logger.info("Local network (no internet): %s", ip)
                return

        # Try all remembered WiFi networks
        logger.info("Trying saved WiFi networks (%d saved)...", len(self._get_saved_wifi()))
        if self._try_saved_networks():
            return

        # Nothing worked -- start AP mode for setup
        logger.info("No known network available, starting AP mode")
        self.start_ap()

    def _monitor_loop(self):
        """Periodically check connectivity. If lost, try saved networks before falling back to AP."""
        while self._running:
            time.sleep(30)

            if self._mode == "ap":
                # While in AP mode, periodically check if a known network appeared
                if self._try_saved_networks():
                    logger.info("Reconnected from AP mode to: %s", self._connected_ssid)
                continue

            if not self.has_internet():
                logger.warning("Internet lost (was on %s), trying saved networks...", self._connected_ssid)
                if self._try_saved_networks():
                    logger.info("Reconnected to: %s", self._connected_ssid)
                else:
                    logger.warning("No saved networks available, starting AP mode")
                    self.start_ap()

    def start(self):
        self._running = True
        self.setup_network()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="network-monitor")
        self._monitor_thread.start()

    def stop(self):
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @property
    def connected_ssid(self) -> str:
        return self._connected_ssid
