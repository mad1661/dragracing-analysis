#!/usr/bin/env python3
"""
Web interface for the NHRA Timing Pi.

Two modes:
  SETUP  — captive portal on hotspot (WiFi network picker)
  ONLINE — local dashboard on the network (status, live data, WiFi settings)

Accessible at http://nhra-timing.local after WiFi connects.
"""

import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from flask import Flask, request, redirect, render_template_string, jsonify, send_file

import wifi_manager

EVENT_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event.json")
DEVICE_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.json")

log = logging.getLogger("web")

app = Flask(__name__)


@app.route("/assets/nhra-logo")
def nhra_logo():
    return send_file(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "nhra-logo.png"),
        mimetype="image/png",
        max_age=3600,
    )


# ── Shared state ──

_mode = "setup"  # "setup" or "online"
_on_connected = None
_on_port_change = None
_on_test_serial = None
_data_buffer = deque(maxlen=200)
_event_config = {"track": "", "race": "", "promoter": ""}


def load_event_config():
    global _event_config
    try:
        with open(EVENT_CONFIG_FILE, "r") as f:
            _event_config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _event_config


def save_event_config(cfg):
    global _event_config
    _event_config = cfg
    with open(EVENT_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_event_config():
    return dict(_event_config)


_device_config = {"port": None, "baud": None}


def load_device_config():
    global _device_config
    try:
        with open(DEVICE_CONFIG_FILE, "r") as f:
            _device_config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return _device_config


def save_device_config(port=None, baud=None, tcp_host=None, tcp_port=None, input_mode=None, secondary_url=None, kiosk_autostart=None):
    global _device_config
    if port is not None:
        _device_config["port"] = port
    if baud is not None:
        _device_config["baud"] = baud
    if tcp_host is not None:
        _device_config["tcp_host"] = tcp_host
    if tcp_port is not None:
        _device_config["tcp_port"] = tcp_port
    if input_mode is not None:
        _device_config["input_mode"] = input_mode
    if secondary_url is not None:
        _device_config["secondary_url"] = secondary_url
    if kiosk_autostart is not None:
        _device_config["kiosk_autostart"] = kiosk_autostart
    with open(DEVICE_CONFIG_FILE, "w") as f:
        json.dump(_device_config, f, indent=2)


def get_device_config():
    return dict(_device_config)
_status = {
    "wifi_ssid": None,
    "wifi_ip": None,
    "tailscale_ip": None,
    "serial_port": None,
    "serial_status": "Not started",
    "tcp_status": "Not started",
    "input_mode": "both",
    "uptime_start": time.time(),
    "lines_received": 0,
    "api_url": None,
    "secondary_url": None,
}
_status_lock = threading.Lock()


_on_tcp_change = None
_on_mode_change = None

def set_on_connected(callback):
    global _on_connected
    _on_connected = callback


def set_on_mode_change(callback):
    global _on_mode_change
    _on_mode_change = callback


def set_on_tcp_change(callback):
    global _on_tcp_change
    _on_tcp_change = callback


def set_on_port_change(callback):
    """Register callback(new_port) for when the serial port is changed via the UI."""
    global _on_port_change
    _on_port_change = callback


def set_on_test_serial(callback):
    """Register callback() to inject test data through the full pipeline (including OLED)."""
    global _on_test_serial
    _on_test_serial = callback


def set_mode(mode):
    global _mode
    _mode = mode


def update_status(**kwargs):
    with _status_lock:
        _status.update(kwargs)


def push_data_line(line):
    _data_buffer.append({"time": time.strftime("%H:%M:%S"), "data": line})
    with _status_lock:
        _status["lines_received"] = _status.get("lines_received", 0) + 1
    raw = line
    if raw.startswith("[TEST] "):
        raw = raw[7:]
    elif raw.startswith("[TEST-XML] "):
        raw = raw[11:]
    _race_parser.feed_line(raw)


# ── Race Parser ──

class RaceParser:
    """Parses raw timing data (serial protocol + XML TimeSlip) into structured race state."""

    def __init__(self):
        self._current = self._empty_race()
        self._completed = deque(maxlen=50)
        self._lock = threading.Lock()
        self._xml_lines = []
        self._in_xml = False

    @staticmethod
    def _empty_race():
        return {
            "category": "", "category_num": "", "round": "",
            "left": {
                "name": "", "car_num": "", "class_name": "",
                "member_num": "", "qual": "", "dial_in": "",
                "rt": "", "sixty": "", "three30": "",
                "six60": "", "six60_speed": "",
                "thousand": "", "thousand_speed": "",
                "et": "", "speed": "", "mov": "",
            },
            "right": {
                "name": "", "car_num": "", "class_name": "",
                "member_num": "", "qual": "", "dial_in": "",
                "rt": "", "sixty": "", "three30": "",
                "six60": "", "six60_speed": "",
                "thousand": "", "thousand_speed": "",
                "et": "", "speed": "", "mov": "",
            },
            "margin": "",
            "winner": "",
            "complete": False,
            "timestamp": "",
        }

    @staticmethod
    def _is_blank_dial(val):
        """Check if a dial-in value is blank (spaces, dots, dashes)."""
        cleaned = val.strip().replace(".", "").replace("-", "").replace(" ", "")
        return len(cleaned) == 0

    def _finish_current(self):
        """Archive current race if it has meaningful data."""
        cur = self._current
        has_data = (cur["left"]["name"] or cur["right"]["name"] or
                    cur["left"]["rt"] or cur["right"]["rt"] or cur["category"])
        if has_data and not cur["complete"]:
            cur["complete"] = True
            cur["timestamp"] = cur["timestamp"] or time.strftime("%H:%M:%S")
            self._completed.append(self._deep_copy(cur))

    def feed_line(self, line):
        s = line.strip()
        if not s:
            return
        with self._lock:
            if "<TimeSlip>" in s:
                self._in_xml = True
                self._xml_lines = [s]
                return
            if self._in_xml:
                self._xml_lines.append(s)
                if "</TimeSlip>" in s:
                    self._parse_xml("\n".join(self._xml_lines))
                    self._in_xml = False
                    self._xml_lines = []
                return
            self._parse_serial(s)

    def _parse_serial(self, s):
        # S5:D / S6:D — dial-in values (S5=left, S6=right)
        if s.startswith("S5:D"):
            val = s[4:].strip()
            if self._is_blank_dial(val):
                self._finish_current()
                self._current = self._empty_race()
                self._current["timestamp"] = time.strftime("%H:%M:%S")
            else:
                self._current["left"]["dial_in"] = val
            return
        if s.startswith("S6:D"):
            val = s[4:].strip()
            if not self._is_blank_dial(val):
                self._current["right"]["dial_in"] = val
            return

        # L+/R+ — set dial-ins from previous ET
        if s.startswith("L+") or s.startswith("R+"):
            return

        # C line — category/class/round (e.g. C13,STOCK ELIMINATOR,Q1)
        if len(s) >= 2 and s[0] == "C" and (s[1].isdigit() or (len(s) > 2 and s[1] == " " and s[2].isdigit())):
            self._finish_current()
            self._current = self._empty_race()
            parts = s.split(",", 2)
            cat_num = parts[0][1:].strip()
            self._current["category_num"] = cat_num
            if len(parts) >= 2:
                self._current["category"] = parts[1].strip()
            if len(parts) >= 3:
                self._current["round"] = parts[2].strip()
            self._current["timestamp"] = time.strftime("%H:%M:%S")
            return

        # Car numbers
        if s.startswith("c1"):
            self._current["left"]["car_num"] = s[2:].strip()
            return
        if s.startswith("c2"):
            self._current["right"]["car_num"] = s[2:].strip()
            return

        # Class codes
        if s.startswith("b1"):
            self._current["left"]["class_name"] = s[2:].strip()
            return
        if s.startswith("b2"):
            self._current["right"]["class_name"] = s[2:].strip()
            return

        # Driver names
        if s.startswith("n1"):
            self._current["left"]["name"] = s[2:].strip()
            return
        if s.startswith("n2"):
            self._current["right"]["name"] = s[2:].strip()
            return

        # Membership numbers
        if s.startswith("m1"):
            self._current["left"]["member_num"] = s[2:].strip()
            return
        if s.startswith("m2"):
            self._current["right"]["member_num"] = s[2:].strip()
            return

        # m- margin line (appears after fl/fr in eliminations)
        if s.startswith("m-"):
            self._current["margin"] = s[2:].strip()
            return

        # Qualifying position
        if s.startswith("q1"):
            self._current["left"]["qual"] = s[2:].strip()
            return
        if s.startswith("q2"):
            self._current["right"]["qual"] = s[2:].strip()
            return

        # Timing data — L0-L7 (left), R0-R7 (right)
        if len(s) >= 2 and s[0] in ("L", "R") and s[1].isdigit():
            side = "left" if s[0] == "L" else "right"
            code = s[1]
            val = s[2:].strip()
            field_map = {
                "0": "rt", "1": "sixty", "2": "three30",
                "3": "six60", "4": "six60_speed",
                "5": "thousand", "6": "et", "7": "speed",
            }
            field = field_map.get(code)
            if field:
                self._current[side][field] = val
            return

        # Winner
        if s.startswith("w-"):
            self._current["winner"] = s[2:].strip().upper()
            return

        # fl/fr/fL/fR — finish time / MOV, marks race complete
        sl = s.lower()
        if sl.startswith("fl") and len(s) > 2 and (s[2].isdigit() or s[2] == " " or s[2] == "."):
            self._current["left"]["mov"] = s[2:].strip()
            self._current["complete"] = True
            self._current["timestamp"] = self._current["timestamp"] or time.strftime("%H:%M:%S")
            self._completed.append(self._deep_copy(self._current))
            return
        if sl.startswith("fr") and len(s) > 2 and (s[2].isdigit() or s[2] == " " or s[2] == "."):
            self._current["right"]["mov"] = s[2:].strip()
            self._current["complete"] = True
            self._current["timestamp"] = self._current["timestamp"] or time.strftime("%H:%M:%S")
            self._completed.append(self._deep_copy(self._current))
            return

    def _parse_xml(self, xml_text):
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            race = self._empty_race()
            race["category"] = (root.findtext("Category") or "").strip()
            race["round"] = (root.findtext("Rnd") or "").strip()
            race["timestamp"] = (root.findtext("TimeStamp") or time.strftime("%H:%M:%S")).strip()
            for side, key in [("Left", "left"), ("Right", "right")]:
                elem = root.find(side)
                if elem is not None:
                    race[key]["name"] = (elem.findtext("Name") or "").strip()
                    race[key]["car_num"] = (elem.findtext("CarNumber") or "").strip()
                    race[key]["class_name"] = (elem.findtext("Class") or "").strip()
                    race[key]["member_num"] = (elem.findtext("MemberNum") or "").strip()
                    race[key]["dial_in"] = (elem.findtext("DialIn") or "").strip()
                    race[key]["qual"] = (elem.findtext("QualPos") or "").strip()
                    race[key]["rt"] = (elem.findtext("RT") or "").strip()
                    race[key]["sixty"] = (elem.findtext("ft60") or "").strip()
                    race[key]["three30"] = (elem.findtext("ft330") or "").strip()
                    race[key]["six60"] = (elem.findtext("ft660") or "").strip()
                    race[key]["six60_speed"] = (elem.findtext("mph660") or "").strip()
                    race[key]["thousand"] = (elem.findtext("ft1000") or "").strip()
                    race[key]["thousand_speed"] = (elem.findtext("mph1000") or "").strip()
                    race[key]["et"] = (elem.findtext("ft1320") or "").strip()
                    race[key]["speed"] = (elem.findtext("mph1320") or "").strip()
                    if (elem.findtext("Win") or "").strip().upper() == "W":
                        race["winner"] = side.upper()
            race["complete"] = True
            self._current = race
            self._completed.append(self._deep_copy(race))
        except Exception as e:
            log.debug("XML parse error: %s", e)

    @staticmethod
    def _deep_copy(race):
        return {
            "category": race["category"],
            "category_num": race.get("category_num", ""),
            "round": race["round"],
            "left": dict(race["left"]),
            "right": dict(race["right"]),
            "margin": race.get("margin", ""),
            "winner": race["winner"],
            "complete": race["complete"],
            "timestamp": race["timestamp"],
        }

    def get_state(self):
        with self._lock:
            return self._deep_copy(self._current)

    def get_completed(self):
        with self._lock:
            return [self._deep_copy(r) for r in self._completed]


_race_parser = RaceParser()


# ── Shared CSS ──

SHARED_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #0d0d1a;
  color: #e8e8f0;
  min-height: 100vh;
}
.container { max-width: 520px; margin: 0 auto; padding: 1rem; }
h1 {
  font-size: 1.4rem;
  color: #d4a017;
  text-align: center;
  margin: 1rem 0 0.5rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.subtitle { text-align: center; color: #8888aa; font-size: 0.85rem; margin-bottom: 1.5rem; }
.card {
  background: #151528;
  border: 1px solid #2a2a4a;
  border-radius: 8px;
  padding: 1rem;
  margin-bottom: 1rem;
}
.card-title {
  font-size: 0.75rem;
  color: #d4a017;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.75rem;
  font-weight: 700;
}
.row { display: flex; justify-content: space-between; padding: 0.3rem 0; }
.row-label { color: #8888aa; font-size: 0.85rem; }
.row-value { font-weight: 600; font-size: 0.85rem; }
.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 3px;
  font-size: 0.7rem;
  font-weight: 700;
}
.badge-ok { background: rgba(0,230,118,0.15); color: #00e676; }
.badge-err { background: rgba(255,82,82,0.15); color: #ff5252; }
.badge-warn { background: rgba(212,160,23,0.15); color: #d4a017; }
.mono {
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 0.8rem;
}
.btn {
  display: block; width: 100%;
  padding: 0.7rem; background: #d4a017; color: #000;
  border: none; border-radius: 6px; font-size: 0.9rem;
  font-weight: 700; cursor: pointer; text-transform: uppercase;
  letter-spacing: 0.05em; margin-top: 0.75rem; text-align: center;
  text-decoration: none;
}
.btn:disabled { opacity: 0.5; cursor: default; }
.btn:hover:not(:disabled) { background: #e8b420; }
.btn-secondary {
  background: transparent; border: 1px solid #2a2a4a;
  color: #8888aa; font-size: 0.8rem; padding: 0.5rem;
}
.data-feed {
  max-height: 350px; overflow-y: auto;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 0.78rem; line-height: 1.5;
  background: #0a0a16; border-radius: 6px; padding: 0.75rem;
}
.data-line { color: #e8e8f0; }
.data-time { color: #555580; margin-right: 0.5rem; }
.nav { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.nav a {
  flex: 1; text-align: center; padding: 0.5rem;
  border: 1px solid #2a2a4a; border-radius: 6px;
  color: #8888aa; text-decoration: none; font-size: 0.8rem;
  font-weight: 600; transition: all 0.15s;
}
.nav a:hover, .nav a.active { border-color: #d4a017; color: #d4a017; }
input[type="password"], input[type="text"] {
  width: 100%; padding: 0.7rem;
  background: #0a0a16; border: 1px solid #2a2a4a;
  border-radius: 6px; color: #e8e8f0; font-size: 1rem;
}
input:focus { outline: none; border-color: #d4a017; }
label {
  display: block; font-size: 0.75rem; color: #8888aa;
  margin: 0.75rem 0 0.3rem; text-transform: uppercase; letter-spacing: 0.05em;
}
.network-list { list-style: none; }
.network {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.8rem 1rem; margin-bottom: 0.5rem;
  background: #151528; border: 1px solid #2a2a4a;
  border-radius: 8px; cursor: pointer; transition: border-color 0.15s;
}
.network:hover, .network.selected { border-color: #d4a017; }
.network-name { font-weight: 600; font-size: 0.9rem; }
.network-meta { font-size: 0.75rem; color: #8888aa; }
.hidden { display: none; }
"""


# ══════════════════════════════════════════════
#  SETUP MODE (captive portal)
# ══════════════════════════════════════════════

SETUP_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing — WiFi Setup</title>
<style>""" + SHARED_CSS + """</style>
</head><body>
<div class="container">
  <h1>NHRA Timing</h1>
  <p class="subtitle">Select a WiFi network to connect</p>
  <ul class="network-list" id="networks">
    {% for net in networks %}
    <li class="network" onclick="selectNetwork(this, '{{ net.ssid }}')">
      <span class="network-name">{{ net.ssid }}</span>
      <span class="network-meta">{{ net.signal }}% · {{ net.security }}</span>
    </li>
    {% endfor %}
  </ul>
  <button class="btn btn-secondary" onclick="location.reload()">Rescan</button>
  <form id="connect-form" class="hidden" method="POST" action="/connect" onsubmit="onSubmit()">
    <label>Network</label>
    <input type="text" id="ssid" name="ssid" readonly>
    <label>Password</label>
    <input type="password" id="password" name="password" placeholder="Enter WiFi password">
    <button type="submit" class="btn" id="connect-btn">Connect</button>
  </form>
  <div id="status" class="hidden" style="text-align:center;padding:1rem;margin-top:1rem;color:#d4a017;font-weight:600;"></div>
</div>
<script>
function selectNetwork(el, ssid) {
  document.querySelectorAll('.network').forEach(n => n.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('ssid').value = ssid;
  document.getElementById('connect-form').classList.remove('hidden');
  document.getElementById('password').focus();
}
function onSubmit() {
  document.getElementById('connect-btn').disabled = true;
  document.getElementById('connect-btn').textContent = 'Connecting...';
  var s = document.getElementById('status');
  s.textContent = 'Connecting to ' + document.getElementById('ssid').value + '...';
  s.classList.remove('hidden');
}
</script>
</body></html>"""

SETUP_RESULT_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing</title>
<style>""" + SHARED_CSS + """
.result { text-align: center; padding: 3rem 1rem; }
</style></head><body>
<div class="container result">
  <h1>NHRA Timing</h1>
  {% if success %}
  <div class="card" style="background:rgba(0,230,118,0.08);border-color:rgba(0,230,118,0.3);">
    <p style="font-size:1.1rem;font-weight:700;color:#00e676;">Connected to {{ ssid }}</p>
    <p style="margin-top:0.5rem;color:#8888aa;">IP: <strong style="color:#e8e8f0;">{{ ip }}</strong></p>
    <p style="margin-top:1rem;color:#8888aa;font-size:0.85rem;">
      Access the dashboard at<br><strong style="color:#d4a017;">http://{{ ip }}</strong>
    </p>
  </div>
  {% else %}
  <div class="card" style="background:rgba(255,82,82,0.08);border-color:rgba(255,82,82,0.3);">
    <p style="font-size:1.1rem;font-weight:700;color:#ff5252;">Failed to connect to {{ ssid }}</p>
  </div>
  <a href="/" class="btn">Try Again</a>
  {% endif %}
</div>
</body></html>"""


# ══════════════════════════════════════════════
#  ONLINE MODE (local dashboard)
# ══════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing Pi</title>
<style>""" + SHARED_CSS + """</style>
</head><body>
<div class="container">
  <h1>NHRA Timing Pi</h1>
  <div class="nav">
    <a href="/" class="active">Dashboard</a>
    <a href="/data">Live Data</a>
    <a href="/wifi">WiFi</a>
    <a href="/settings">Settings</a>
  </div>

  <div class="card">
    <div class="card-title">System Status</div>
    <div class="row">
      <span class="row-label">WiFi</span>
      <span class="row-value" id="wifi-ssid">{{ status.wifi_ssid or '—' }}
        {% if status.wifi_ip %}<span class="badge badge-ok">Connected</span>{% else %}<span class="badge badge-err">Offline</span>{% endif %}
      </span>
    </div>
    <div class="row">
      <span class="row-label">IP Address</span>
      <span class="row-value mono">{{ status.wifi_ip or '—' }}</span>
    </div>
    <div class="row">
      <span class="row-label">Serial Port</span>
      <span class="row-value mono">{{ status.serial_port or '—' }}</span>
    </div>
    <div class="row">
      <span class="row-label">Serial Status</span>
      <span class="row-value">{{ status.serial_status }}</span>
    </div>
    <div class="row">
      <span class="row-label">Lines Received</span>
      <span class="row-value mono" id="line-count">{{ status.lines_received }}</span>
    </div>
    <div class="row">
      <span class="row-label">Uptime</span>
      <span class="row-value" id="uptime">{{ uptime }}</span>
    </div>
    <div class="row">
      <span class="row-label">Firebase API</span>
      <span class="row-value mono" style="font-size:0.7rem;">{{ status.api_url or '—' }}</span>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Quick Links</div>
    <a href="https://nhra-timing-api.web.app" target="_blank" class="btn">Open Live Timing Web App</a>
    <a href="/data" class="btn btn-secondary" style="margin-top:0.5rem;display:block;">View Raw Serial Data</a>
  </div>
</div>
<script>
setInterval(function() {
  fetch('/api/status').then(r => r.json()).then(d => {
    document.getElementById('line-count').textContent = d.lines_received;
    document.getElementById('uptime').textContent = d.uptime;
  }).catch(() => {});
}, 3000);
</script>
</body></html>"""


DATA_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing Pi — Live Data</title>
<style>""" + SHARED_CSS + """</style>
</head><body>
<div class="container">
  <h1>NHRA Timing Pi</h1>
  <div class="nav">
    <a href="/">Dashboard</a>
    <a href="/data" class="active">Live Data</a>
    <a href="/wifi">WiFi</a>
    <a href="/settings">Settings</a>
  </div>

  <div class="card">
    <div class="card-title">
      Raw Serial Feed
      <span class="badge badge-ok" id="live-badge" style="margin-left:0.5rem;">LIVE</span>
    </div>
    <div class="data-feed" id="feed">
      {% for item in data %}
      <div class="data-line"><span class="data-time">{{ item.time }}</span>{{ item.data }}</div>
      {% endfor %}
      {% if not data %}
      <div class="data-line" style="color:#555580;">Waiting for data...</div>
      {% endif %}
    </div>
  </div>
  <button class="btn btn-secondary" onclick="clearFeed()">Clear</button>
</div>
<script>
var feed = document.getElementById('feed');
var lastCount = {{ data|length }};
function poll() {
  fetch('/api/data?since=' + lastCount).then(r => r.json()).then(d => {
    d.lines.forEach(function(item) {
      var div = document.createElement('div');
      div.className = 'data-line';
      div.innerHTML = '<span class="data-time">' + item.time + '</span>' + item.data;
      feed.appendChild(div);
    });
    if (d.lines.length > 0) {
      lastCount += d.lines.length;
      feed.scrollTop = feed.scrollHeight;
    }
  }).catch(() => {});
}
setInterval(poll, 1000);
feed.scrollTop = feed.scrollHeight;
function clearFeed() { 
  fetch('/api/clear-data', {method: 'POST'}).then(() => {
    feed.innerHTML = '<div class="data-line" style="color:#555580;">Waiting for data...</div>'; 
    lastCount = 0; 
  });
}
</script>
</body></html>"""


WIFI_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing Pi — WiFi</title>
<style>""" + SHARED_CSS + """</style>
</head><body>
<div class="container">
  <h1>NHRA Timing Pi</h1>
  <div class="nav">
    <a href="/">Dashboard</a>
    <a href="/data">Live Data</a>
    <a href="/wifi" class="active">WiFi</a>
    <a href="/settings">Settings</a>
  </div>

  <div class="card">
    <div class="card-title">Current Connection</div>
    <div class="row">
      <span class="row-label">Network</span>
      <span class="row-value">{{ current_ssid or 'Not connected' }}
        {% if current_ssid %}<span class="badge badge-ok">Connected</span>{% endif %}
      </span>
    </div>
    <div class="row">
      <span class="row-label">IP</span>
      <span class="row-value mono">{{ current_ip or '—' }}</span>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Change Network</div>
    <ul class="network-list">
      {% for net in networks %}
      <li class="network" onclick="selectNetwork(this, '{{ net.ssid }}')">
        <span class="network-name">{{ net.ssid }}{% if net.ssid == current_ssid %} <span class="badge badge-ok">current</span>{% endif %}</span>
        <span class="network-meta">{{ net.signal }}%</span>
      </li>
      {% endfor %}
    </ul>
    <button class="btn btn-secondary" onclick="location.reload()">Rescan</button>
    <form id="connect-form" class="hidden" method="POST" action="/connect">
      <label>Network</label>
      <input type="text" id="ssid" name="ssid" readonly>
      <label>Password</label>
      <input type="password" name="password" placeholder="Enter password">
      <input type="hidden" name="from" value="wifi">
      <button type="submit" class="btn">Switch Network</button>
    </form>
  </div>
</div>
<script>
function selectNetwork(el, ssid) {
  document.querySelectorAll('.network').forEach(n => n.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('ssid').value = ssid;
  document.getElementById('connect-form').classList.remove('hidden');
}
</script>
</body></html>"""


SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NHRA Timing Pi — Settings</title>
<style>""" + SHARED_CSS + """
select {
  width: 100%; padding: 0.7rem;
  background: #0a0a16; border: 1px solid #2a2a4a;
  border-radius: 6px; color: #e8e8f0; font-size: 1rem;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
}
select:focus { outline: none; border-color: #d4a017; }
.port-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.8rem 1rem; margin-bottom: 0.5rem;
  background: #151528; border: 1px solid #2a2a4a;
  border-radius: 8px; cursor: pointer; transition: border-color 0.15s;
}
.port-item:hover, .port-item.selected { border-color: #d4a017; }
.port-item.active { border-color: #00e676; }
.port-device { font-weight: 600; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 0.9rem; }
.port-desc { font-size: 0.75rem; color: #8888aa; margin-top: 0.2rem; }
.msg {
  text-align: center; padding: 0.75rem; border-radius: 6px;
  font-weight: 600; font-size: 0.85rem; margin-bottom: 1rem;
}
.msg-ok { background: rgba(0,230,118,0.1); color: #00e676; }
.msg-err { background: rgba(255,82,82,0.1); color: #ff5252; }
</style>
</head><body>
<div class="container">
  <h1>NHRA Timing Pi</h1>
  <div class="nav">
    <a href="/">Dashboard</a>
    <a href="/data">Live Data</a>
    <a href="/wifi">WiFi</a>
    <a href="/settings" class="active">Settings</a>
  </div>

  {% if message %}
  <div class="msg {{ 'msg-ok' if msg_ok else 'msg-err' }}">{{ message }}</div>
  {% endif %}

  <div class="card">
    <div class="card-title">Connections</div>

    <!-- SERIAL SECTION -->
    <div style="background:#111; border:1px solid #333; border-radius:6px; padding:0.75rem; margin-bottom:1rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
        <div style="font-weight:800; color:#d4a017; font-size:0.9rem;">SERIAL (USB)</div>
        <form method="POST" action="/settings/toggle-mode">
          <input type="hidden" name="toggle" value="serial">
          <button type="submit" class="btn" style="padding:0.2rem 0.75rem; font-weight:800; {{ 'background:rgba(0,230,118,0.2);color:#00e676;border-color:#00e676;' if input_mode in ['serial', 'both'] else 'color:#777;' }}">
            {{ 'ON' if input_mode in ['serial', 'both'] else 'OFF' }}
          </button>
        </form>
      </div>
      
      <div style="display:{{ 'block' if input_mode in ['serial', 'both'] else 'none' }};">
        <div style="font-size:0.7rem; color:#8888aa; margin-bottom:0.5rem;">Status: <span style="color:{{ '#00e676' if 'Listen' in serial_status else '#ff5252' }}">{{ serial_status }}</span></div>
        <form method="POST" action="/settings/port" style="display:flex; gap:0.5rem; flex-wrap:wrap;">
          <input list="detected-ports" name="port" value="{{ current_port or '' }}" placeholder="Port (e.g. /dev/ttyUSB0)" style="flex:2; min-width:120px; padding:0.4rem; border:1px solid #333; border-radius:4px; background:#1a1a1a; color:#e0e0e0; font-family:monospace; font-size:0.75rem;">
          <datalist id="detected-ports">
            {% for p in ports %}
            <option value="{{ p.device }}">{{ p.description[:20] }}</option>
            {% endfor %}
          </datalist>
          <select name="baud" style="flex:1; min-width:80px; padding:0.4rem; border:1px solid #333; border-radius:4px; background:#1a1a1a; color:#e0e0e0; font-size:0.75rem;">
            {% for rate in [4800, 9600, 19200, 38400, 115200] %}
            <option value="{{ rate }}" {{ 'selected' if rate == baud_rate else '' }}>{{ rate }}</option>
            {% endfor %}
          </select>
          <button type="submit" class="btn btn-secondary" style="padding:0.4rem 0.75rem;">Save</button>
        </form>
      </div>
    </div>

    <!-- TCP SECTION -->
    <div style="background:#111; border:1px solid #333; border-radius:6px; padding:0.75rem;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
        <div style="font-weight:800; color:#d4a017; font-size:0.9rem;">TCP/IP (NETWORK)</div>
        <form method="POST" action="/settings/toggle-mode">
          <input type="hidden" name="toggle" value="tcp">
          <button type="submit" class="btn" style="padding:0.2rem 0.75rem; font-weight:800; {{ 'background:rgba(0,230,118,0.2);color:#00e676;border-color:#00e676;' if input_mode in ['tcp', 'both'] else 'color:#777;' }}">
            {{ 'ON' if input_mode in ['tcp', 'both'] else 'OFF' }}
          </button>
        </form>
      </div>

      <div style="display:{{ 'block' if input_mode in ['tcp', 'both'] else 'none' }};">
        <div style="font-size:0.7rem; color:#8888aa; margin-bottom:0.5rem;">Status: <span style="color:{{ '#00e676' if 'Listen' in tcp_status or 'connect' in tcp_status|lower else '#ff5252' }}">{{ tcp_status or 'Not configured' }}</span></div>
        <form method="POST" action="/settings/tcp" style="display:flex; gap:0.5rem; flex-wrap:wrap;">
          <input type="text" name="tcp_host" value="{{ tcp_host or '' }}" placeholder="IP (0.0.0.0 to listen)" style="flex:2; min-width:120px; padding:0.4rem; border:1px solid #333; border-radius:4px; background:#1a1a1a; color:#e0e0e0; font-family:monospace; font-size:0.75rem;">
          <input type="number" name="tcp_port" value="{{ tcp_port or '' }}" placeholder="Port" style="flex:1; min-width:60px; padding:0.4rem; border:1px solid #333; border-radius:4px; background:#1a1a1a; color:#e0e0e0; font-size:0.75rem;">
          <button type="submit" class="btn btn-secondary" style="padding:0.4rem 0.75rem;">Save</button>
        </form>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Event Info & Destinations</div>
    <form method="POST" action="/settings/event" style="display:flex;flex-direction:column;gap:1rem;">
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:flex-end;">
        <label style="flex:2;min-width:140px;">
          <span style="font-size:0.75rem;color:#8888aa;display:block;margin-bottom:0.2rem;">Track Name</span>
          <input type="text" name="track" value="{{ event.track or '' }}" placeholder="Track">
        </label>
        <label style="flex:2;min-width:140px;">
          <span style="font-size:0.75rem;color:#8888aa;display:block;margin-bottom:0.2rem;">Event Name</span>
          <input type="text" name="race" value="{{ event.race or '' }}" placeholder="Event">
        </label>
        <label style="flex:1.5;min-width:100px;">
          <span style="font-size:0.75rem;color:#8888aa;display:block;margin-bottom:0.2rem;">Promoter</span>
          <input type="text" name="promoter" value="{{ event.promoter or '' }}" placeholder="NHRA">
        </label>
      </div>
      <div style="border-top:1px solid #333;padding-top:1rem;display:flex;flex-direction:column;gap:0.5rem;">
        <label style="font-size:0.75rem;color:#8888aa;font-weight:700;text-transform:uppercase;">Additional API / Webhook URL</label>
        <p style="color:#555580;font-size:0.75rem;margin:0;">Data is always sent to Firebase. Enter a URL below if you want to forward data to a second destination simultaneously.</p>
        <div style="display:flex;gap:0.5rem;">
          <input type="text" name="secondary_url" value="{{ secondary_url or '' }}" placeholder="https://api.yourdomain.com/webhook" style="flex:1;">
          <button type="submit" class="btn" style="height:38px;padding:0 1.5rem;">Save</button>
        </div>
      </div>
    </form>
  </div>

  <div class="card">
    <div class="card-title">Data Logging</div>
    <div id="log-stats" style="display:flex;flex-wrap:wrap;gap:0.75rem;font-size:0.8rem;margin-bottom:1rem;color:#e0e0e0;">
      <div><span style="color:#8888aa;">Log dir:</span> ~/timing-logs/</div>
      <div><span style="color:#8888aa;">Logs:</span> <span id="log-file-count">—</span></div>
      <div><span style="color:#8888aa;">Lines:</span> <span id="log-line-count">—</span></div>
      <div><span style="color:#8888aa;">Size:</span> <span id="log-size">—</span></div>
      <div><span style="color:#8888aa;">Queued:</span> <span id="queue-count" style="font-weight:700;color:#d4a017;">—</span></div>
    </div>
    
    <div style="display:flex;flex-direction:column;gap:1rem;">
      <form method="POST" action="/settings/flush-queue">
        <button type="submit" class="btn btn-secondary" style="width:100%;" onclick="var b=this;setTimeout(function(){b.disabled=true;b.textContent='Uploading...';},50);">Upload Queued Offline Data</button>
      </form>
      
      <form method="POST" action="/settings/upload-day" style="background:#111;padding:0.75rem;border:1px solid #333;border-radius:6px;">
        <div style="font-size:0.75rem;font-weight:700;color:#d4a017;margin-bottom:0.5rem;text-transform:uppercase;">Send Full Day Log</div>
        <div style="display:flex;gap:0.5rem;align-items:center;">
          <input type="date" name="date" required value="{{ today_date }}" style="flex:1;min-width:120px;padding:0.5rem;border:1px solid #444;border-radius:4px;background:#1a1a1a;color:#fff;font-size:0.9rem;height:38px;box-sizing:border-box;color-scheme:dark;">
          <button type="submit" class="btn btn-secondary" style="height:38px;padding:0 1rem;" onclick="var b=this;setTimeout(function(){b.disabled=true;b.textContent='Sending...';},50);">Send</button>
        </div>
      </form>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Test Signals & System</div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem;">
      <form method="POST" action="/settings/test-api" style="flex:1;min-width:120px;">
        <button type="submit" class="btn" onclick="var b=this;setTimeout(function(){b.disabled=true;b.textContent='Testing...';},50);">Test Firebase</button>
      </form>
      <form method="POST" action="/settings/test-serial" style="flex:1;min-width:120px;">
        <button type="submit" class="btn btn-secondary" onclick="var b=this;setTimeout(function(){b.disabled=true;b.textContent='Sending...';},50);">Test Serial Data</button>
      </form>
      <form method="POST" action="/settings/test-xml" style="flex:1;min-width:120px;">
        <button type="submit" class="btn btn-secondary" onclick="var b=this;setTimeout(function(){b.disabled=true;b.textContent='Sending...';},50);">Test TCP Data</button>
      </form>
      <form method="POST" action="/settings/restart" style="flex:1;min-width:120px;">
        <button type="submit" class="btn btn-secondary" style="border-color:#ff5252;color:#ff5252;">Restart System</button>
      </form>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Kiosk Mode</div>
    <p style="color:#8888aa;font-size:0.8rem;margin-bottom:0.75rem;">Full-screen racing display for HDMI screens and mobile devices.</p>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
      <a href="/kiosk" target="_blank" class="btn" style="flex:1;min-width:170px;">Launch Kiosk Mode</a>
      <form method="POST" action="/settings/kiosk-stop" style="flex:1;min-width:170px;">
        <button type="submit" class="btn btn-secondary" style="border-color:#ffb347;color:#ffb347;">Exit Kiosk Mode</button>
      </form>
    </div>
    <p style="color:#8888aa;font-size:0.75rem;margin-top:0.75rem;">Use the Timing Talk Kiosk icon on the Pi desktop to start it again without rebooting.</p>
    <form method="POST" action="/settings/kiosk-autostart" style="margin-top:0.75rem;">
      <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer;">
        <input type="checkbox" name="kiosk_autostart" value="1" {{ 'checked' if kiosk_autostart else '' }} onchange="this.form.submit();">
        <span style="font-size:0.8rem;">Auto-launch on HDMI at boot</span>
      </label>
    </form>
  </div>
</div>
<script>
(function(){
  fetch('/api/log-stats').then(function(r){return r.json()}).then(function(d){
    document.getElementById('log-file-count').textContent=d.files||0;
    document.getElementById('log-line-count').textContent=d.total_lines||0;
    document.getElementById('log-size').textContent=(d.total_size_kb||0)+' KB';
    document.getElementById('queue-count').textContent=d.queued||0;
  }).catch(function(){});
})();
</script>
</body></html>"""


# ══════════════════════════════════════════════
#  KIOSK MODE
# ══════════════════════════════════════════════

KIOSK_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>NHRA Timing Kiosk</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent;}
:root{
  --bg:#040814;--surface:#0c1328;--surface-2:#101a36;--card:rgba(13,21,43,0.9);
  --border:rgba(255,255,255,0.12);--gold:#ffd166;--gold-dim:rgba(255,209,102,0.25);
  --text:#f7f9ff;--dim:#98a4cc;--green:#4ade80;--red:#ff5c70;--blue:#3357ff;--accent:#ff304f;
  --accent-soft:rgba(255,48,79,0.15);--blue-soft:rgba(51,87,255,0.16);--shadow:0 24px 60px rgba(0,0,0,0.38);
  --mono:'SF Mono','Fira Code','Consolas',monospace;
  --display:'Avenir Next Condensed','DIN Alternate','Arial Narrow',sans-serif;
  --sans:'Avenir Next','Segoe UI',Roboto,sans-serif;
}
body[data-theme='light']{
  --bg:#eef3fb;--surface:#ffffff;--surface-2:#e3ebf6;--card:#ffffff;
  --border:rgba(16,38,77,0.14);--gold:#c87400;--gold-dim:rgba(200,116,0,0.22);
  --text:#13233f;--dim:#62738d;--green:#17803d;--red:#c83349;--blue:#2457d6;--accent:#cf2f5e;
  --accent-soft:rgba(207,47,94,0.08);--blue-soft:rgba(36,87,214,0.08);--shadow:0 16px 38px rgba(28,48,83,0.12);
}
html,body{min-height:100%;overflow-x:hidden;overflow-y:auto;background:var(--bg);color:var(--text);font-family:var(--sans);-webkit-overflow-scrolling:touch;overscroll-behavior-y:auto;scroll-padding-bottom:6rem;touch-action:auto;}
body[data-theme='light'] #app{
  background:radial-gradient(circle at top, rgba(36,87,214,0.08), transparent 26%),linear-gradient(180deg,#eef3fb 0%,#dee8f5 100%);
}
body[data-theme='light'] .app-header{
  background:linear-gradient(180deg, rgba(255,255,255,0.98), rgba(240,245,252,0.96));
}
body[data-theme='light'] .app-header h1,
body[data-theme='light'] .race-category,
body[data-theme='light'] .result-cat,
body[data-theme='light'] .setting-title,
body[data-theme='light'] .slip-driver-name,
body[data-theme='light'] .hero-pill-value,
body[data-theme='light'] .setting-value{
  color:var(--text);
}
body[data-theme='light'] .app-header p,
body[data-theme='light'] .race-round,
body[data-theme='light'] .feed-toolbar span,
body[data-theme='light'] .result-time,
body[data-theme='light'] .setting-label,
body[data-theme='light'] .no-results,
body[data-theme='light'] .feed-empty{
  color:var(--dim);
}
body[data-theme='light'] .hero-pill,
body[data-theme='light'] .slip,
body[data-theme='light'] .feed-wrap,
body[data-theme='light'] .result-card,
body[data-theme='light'] .setting-card,
body[data-theme='light'] .no-race{
  background:linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,247,252,0.96));
  border-color:rgba(16,38,77,0.12);
}
body[data-theme='light'] .tab-bar{
  background:rgba(248,251,255,0.95);
  border-top-color:rgba(16,38,77,0.12);
}
body[data-theme='light'] .tab-btn{
  background:rgba(19,35,63,0.04);color:var(--dim);
}
body[data-theme='light'] .tab-btn.active{
  color:var(--text);
  background:linear-gradient(180deg, rgba(207,47,94,0.12), rgba(36,87,214,0.10));
}
body[data-theme='light'] .feed-scroll,
body[data-theme='light'] .result-lane,
body[data-theme='light'] .k-input{
  background:rgba(18,35,63,0.04);
  border-color:rgba(16,38,77,0.12);
}
body[data-theme='light'] .k-btn-secondary{
  background:rgba(18,35,63,0.04);
  color:var(--text);
}
body[data-theme='light'] .feed-time{color:#5570b7;}

/* ── Splash ── */
#splash{
  position:fixed;inset:0;z-index:100;
  display:flex;align-items:center;justify-content:center;
  background:
    radial-gradient(circle at 18% 18%, rgba(255,48,79,0.26), transparent 28%),
    radial-gradient(circle at 82% 20%, rgba(51,87,255,0.28), transparent 26%),
    linear-gradient(160deg,#030611 0%,#071023 38%,#091732 72%,#040814 100%);
  animation:splashOut 0.95s cubic-bezier(.68,-0.2,.32,1) 5.8s forwards;
  overflow:hidden;
}
#splash::before{
  content:'';position:absolute;inset:-20%;
  background:
    repeating-linear-gradient(115deg, transparent 0 42px, rgba(255,255,255,0.035) 42px 44px),
    linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%);
  opacity:0.45;
  transform:perspective(700px) rotateX(73deg) translateY(28%);
  transform-origin:center bottom;
  animation:gridDrift 10s linear infinite;
}
#splash::after{
  content:'';position:absolute;inset:0;
  background:linear-gradient(90deg, transparent 0%, rgba(255,209,102,0.9) 45%, transparent 100%);
  width:45%;filter:blur(10px);transform:translateX(-160%) skewX(-18deg);
  animation:lightSweep 2.1s cubic-bezier(.32,.02,.12,1) 0.55s forwards;
}
.splash-inner{
  position:relative;z-index:1;width:min(96vw,680px);padding:2.15rem 1.35rem 1.6rem;
  text-align:center;animation:splashLift 1s cubic-bezier(.22,.9,.32,1) 0.15s both;
}
.splash-badge{
  display:inline-flex;align-items:center;gap:0.5rem;
  margin-bottom:1rem;padding:0.35rem 0.8rem;border-radius:999px;
  border:1px solid rgba(255,255,255,0.14);background:rgba(255,255,255,0.06);
  color:var(--dim);font-size:0.66rem;font-weight:800;letter-spacing:0.22em;text-transform:uppercase;
  backdrop-filter:blur(10px);
}
.splash-badge::before{
  content:'';width:8px;height:8px;border-radius:50%;
  background:linear-gradient(180deg,var(--gold),#fff0b0);box-shadow:0 0 14px rgba(255,209,102,0.8);
}
.splash-logo-frame{
  position:relative;margin:0 auto 1.25rem;padding:1.05rem 1.05rem;width:min(92vw,540px);
  background:linear-gradient(180deg, rgba(255,255,255,0.18), rgba(255,255,255,0.04));
  border:1px solid rgba(255,255,255,0.14);border-radius:28px;
  box-shadow:0 22px 60px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.22);
  overflow:hidden;backdrop-filter:blur(14px);
}
.splash-logo-frame::before,
.splash-logo-frame::after{
  content:'';position:absolute;top:12px;bottom:12px;width:3px;border-radius:999px;
  background:linear-gradient(180deg, transparent, var(--accent), transparent);
  opacity:0.85;
}
.splash-logo-frame::before{left:12px;}
.splash-logo-frame::after{right:12px;background:linear-gradient(180deg, transparent, var(--blue), transparent);}
.splash-logo{
  display:block;width:100%;height:auto;object-fit:contain;filter:drop-shadow(0 18px 30px rgba(0,0,0,0.28));
  animation:logoPulse 1.9s ease-in-out 0.35s both;
}
.splash-wordmark{
  margin-top:0.95rem;font-family:var(--display);font-size:clamp(2.7rem,10vw,5rem);
  letter-spacing:0.22em;text-transform:uppercase;line-height:0.95;color:#fff;
  text-shadow:0 12px 30px rgba(0,0,0,0.45);
}
.splash-wordmark strong{
  display:inline-block;color:var(--gold);font-weight:900;
  text-shadow:0 0 26px rgba(255,209,102,0.28);
}
.splash-sub{
  margin-top:0.7rem;color:#d7e0ff;font-size:0.9rem;font-weight:700;
  letter-spacing:0.34em;text-transform:uppercase;opacity:0.9;
}
.splash-track{
  position:relative;display:grid;grid-template-columns:repeat(3,1fr);gap:0.6rem;
  width:min(82vw,360px);margin:1.1rem auto 0;
}
.splash-track span{
  position:relative;height:6px;border-radius:999px;overflow:hidden;
  background:rgba(255,255,255,0.08);box-shadow:inset 0 0 0 1px rgba(255,255,255,0.05);
}
.splash-track span::after{
  content:'';position:absolute;inset:0 auto 0 -65%;width:65%;
  background:linear-gradient(90deg, transparent, var(--gold), #fff, transparent);
  animation:laneDash 1.05s cubic-bezier(.52,.03,.36,.98) infinite;
}
.splash-track span:nth-child(2)::after{animation-delay:0.15s;}
.splash-track span:nth-child(3)::after{animation-delay:0.3s;}
@keyframes splashLift{from{opacity:0;transform:translateY(18px) scale(0.96);}to{opacity:1;transform:translateY(0) scale(1);}}
@keyframes splashOut{to{opacity:0;visibility:hidden;pointer-events:none;}}
@keyframes gridDrift{from{transform:perspective(700px) rotateX(73deg) translateY(28%) translateX(0);}to{transform:perspective(700px) rotateX(73deg) translateY(28%) translateX(-60px);}}
@keyframes lightSweep{to{transform:translateX(280%) skewX(-18deg);}}
@keyframes logoPulse{0%{opacity:0;transform:scale(0.9) translateY(8px);}55%{opacity:1;transform:scale(1.03) translateY(0);}100%{opacity:1;transform:scale(1);}}
@keyframes laneDash{to{transform:translateX(255%);}}

/* ── App Shell ── */
#app{
  display:flex;flex-direction:column;min-height:100dvh;opacity:0;
  background:
    radial-gradient(circle at top, rgba(51,87,255,0.12), transparent 28%),
    radial-gradient(circle at bottom, rgba(255,48,79,0.12), transparent 24%),
    linear-gradient(180deg,#050917 0%,#091128 100%);
  animation:fadeIn 0.7s ease 6.15s forwards;
}
@keyframes fadeIn{to{opacity:1;}}
.app-header{
  position:relative;overflow:hidden;flex-shrink:0;
  padding:0.8rem 0.85rem 0.7rem;
  background:linear-gradient(180deg, rgba(12,19,40,0.96), rgba(10,16,34,0.92));
  border-bottom:1px solid var(--border);
  box-shadow:var(--shadow);
}
.app-header::before{
  content:'';position:absolute;left:-10%;right:-10%;top:-75px;height:150px;
  background:radial-gradient(circle, rgba(255,48,79,0.17), transparent 55%);
  opacity:0.85;
}
.app-header::after{
  content:'';position:absolute;right:-30px;top:-36px;width:120px;height:120px;border-radius:50%;
  background:radial-gradient(circle, rgba(51,87,255,0.24), transparent 68%);
}
.header-top{
  position:relative;z-index:1;display:flex;align-items:center;gap:0.8rem;
}
.header-logo{
  width:96px;max-width:30vw;height:auto;flex-shrink:0;filter:drop-shadow(0 10px 18px rgba(0,0,0,0.35));
}
.header-copy{min-width:0;flex:1;text-align:left;}
.header-actions{display:flex;align-items:flex-start;justify-content:flex-end;}
.theme-toggle{
  min-width:88px;padding:0.55rem 0.8rem;border-radius:999px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.08);color:var(--text);font-size:0.62rem;font-weight:900;letter-spacing:0.12em;
  text-transform:uppercase;box-shadow:0 12px 24px rgba(0,0,0,0.16);cursor:pointer;
}
.theme-toggle small{display:block;color:var(--dim);font-size:0.5rem;letter-spacing:0.18em;margin-top:0.1rem;}
.theme-toggle:active{transform:scale(0.97);}
.theme-switcher{
  display:grid;grid-template-columns:1fr 1fr;gap:0.55rem;margin-top:0.2rem;
}
.theme-choice{
  padding:0.78rem 0.75rem;border-radius:16px;border:1px solid rgba(255,255,255,0.1);
  background:rgba(255,255,255,0.04);color:var(--text);font-size:0.76rem;font-weight:800;letter-spacing:0.08em;
  text-transform:uppercase;cursor:pointer;
}
.theme-choice small{display:block;margin-top:0.18rem;color:var(--dim);font-size:0.58rem;letter-spacing:0.08em;text-transform:none;}
.theme-choice.active{
  border-color:var(--gold);box-shadow:0 0 0 2px var(--gold-dim) inset;background:linear-gradient(180deg, rgba(255,209,102,0.12), rgba(255,255,255,0.04));
}
body[data-theme='light'] .theme-toggle,
body[data-theme='light'] .theme-choice{
  background:rgba(18,35,63,0.04);
  border-color:rgba(16,38,77,0.12);
}
.header-kicker{
  display:block;margin-bottom:0.22rem;color:var(--gold);
  font-size:0.58rem;font-weight:800;letter-spacing:0.24em;text-transform:uppercase;
}
.app-header h1{
  font-family:var(--display);font-size:1.5rem;line-height:0.96;font-weight:900;
  letter-spacing:0.08em;text-transform:uppercase;color:#fff;
  text-shadow:0 10px 22px rgba(0,0,0,0.32);
}
.app-header p{
  font-size:0.72rem;color:#d4dcf9;margin-top:0.28rem;
  letter-spacing:0.05em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.hero-status{
  position:relative;z-index:1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
  gap:0.45rem;margin-top:0.75rem;
}
.hero-pill{
  position:relative;overflow:hidden;min-width:0;padding:0.55rem 0.6rem;border-radius:16px;
  background:linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.03));
  border:1px solid rgba(255,255,255,0.1);backdrop-filter:blur(10px);
}
.hero-pill::before{
  content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:linear-gradient(180deg,var(--accent),var(--gold));
}
.hero-pill:nth-child(2)::before{background:linear-gradient(180deg,var(--blue),var(--gold));}
.hero-pill:nth-child(3)::before{background:linear-gradient(180deg,var(--gold),var(--accent));}
.hero-pill-label{
  display:block;color:var(--dim);font-size:0.54rem;font-weight:800;
  letter-spacing:0.18em;text-transform:uppercase;margin-bottom:0.18rem;
}
.hero-pill-value{
  display:flex;align-items:center;gap:0.3rem;min-width:0;
  font-size:0.66rem;font-weight:700;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.tab-content{
  flex:1;overflow:visible;padding:0.7rem 0.7rem 1rem;
  scrollbar-width:none;
}
.tab-content::-webkit-scrollbar{display:none;}
.tab-panel{display:none;animation:panelIn 0.25s ease;}
.tab-panel.active{display:block;}
@keyframes panelIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);}}

/* ── Tab Bar ── */
.tab-bar{
  position:sticky;bottom:0;z-index:25;
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0.45rem;
  padding:0.55rem 0.65rem calc(0.55rem + env(safe-area-inset-bottom,0));
  border-top:1px solid var(--border);
  background:rgba(8,13,28,0.96);backdrop-filter:blur(14px);flex-shrink:0;
}
.tab-btn{
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0.26rem;
  min-height:68px;padding:0.62rem 0.24rem;border-radius:20px;border:1px solid transparent;
  background:rgba(255,255,255,0.03);color:var(--dim);cursor:pointer;
  font-size:0.62rem;font-weight:900;font-family:var(--sans);text-transform:uppercase;
  letter-spacing:0.14em;transition:all 0.2s ease;
}
.tab-btn svg{width:20px;height:20px;transition:transform 0.2s ease;}
.tab-btn.active{
  color:#fff;background:linear-gradient(180deg, rgba(255,48,79,0.2), rgba(51,87,255,0.16));
  border-color:rgba(255,255,255,0.12);box-shadow:0 14px 26px rgba(0,0,0,0.28);
}
.tab-btn.active svg{transform:translateY(-1px);filter:drop-shadow(0 0 6px rgba(255,209,102,0.38));}
.tab-btn:active{transform:scale(0.97);}

/* ── Live Race (Timeslip Layout) ── */
.slip{
  position:relative;background:linear-gradient(180deg, rgba(13,21,43,0.92), rgba(8,13,28,0.98));
  border:1px solid rgba(255,255,255,0.1);border-radius:22px;overflow:hidden;
  box-shadow:var(--shadow);
}
.slip::before{
  content:'';position:absolute;left:0;right:0;top:0;height:3px;
  background:linear-gradient(90deg,var(--accent),var(--gold),var(--blue));
}
.slip-header{
  position:relative;text-align:center;padding:0.78rem 0.85rem 0.72rem;
  background:linear-gradient(135deg, rgba(255,48,79,0.18), rgba(51,87,255,0.14));
  border-bottom:1px solid rgba(255,255,255,0.09);
}
.slip-header::after{
  content:'';position:absolute;left:1rem;right:1rem;bottom:0;height:1px;
  background:linear-gradient(90deg, transparent, rgba(255,209,102,0.65), transparent);
}
.race-category{
  font-family:var(--display);font-size:1.35rem;font-weight:900;color:#fff;
  text-transform:uppercase;letter-spacing:0.08em;
}
.race-round{font-size:0.72rem;color:#d6defb;margin-top:0.16rem;letter-spacing:0.08em;text-transform:uppercase;}
.slip-drivers{
  display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
  border-bottom:1px solid rgba(255,255,255,0.07);
}
.slip-driver{padding:0.68rem 0.72rem 0.62rem;}
.slip-driver.left{box-shadow:inset 3px 0 0 rgba(255,48,79,0.6);}
.slip-driver.right{text-align:right;box-shadow:inset -3px 0 0 rgba(51,87,255,0.75);}
.slip-driver-name{
  font-size:1rem;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff;
}
.slip-driver-meta{font-size:0.65rem;color:var(--dim);margin-top:0.18rem;}
.slip-driver-meta .car-num{color:var(--gold);font-weight:800;}
.slip-driver-meta .class-badge{
  display:inline-flex;align-items:center;background:rgba(255,255,255,0.08);padding:0.08rem 0.38rem;border-radius:999px;
  margin-left:0.32rem;letter-spacing:0.05em;border:1px solid rgba(255,255,255,0.05);
}
.slip-driver.right .class-badge{margin-left:0;margin-right:0.32rem;}
.slip-vs{
  display:flex;align-items:center;justify-content:center;padding:0 0.35rem;
  font-size:0.58rem;color:var(--gold);text-transform:uppercase;letter-spacing:0.18em;
  border-left:1px solid rgba(255,255,255,0.06);border-right:1px solid rgba(255,255,255,0.06);
}
.slip-winner-tag{
  display:inline-block;margin-top:0.24rem;padding:0.16rem 0.45rem;
  background:linear-gradient(180deg,var(--gold),#ffeb99);color:#120f05;font-size:0.52rem;font-weight:900;
  border-radius:999px;text-transform:uppercase;letter-spacing:0.12em;
}
.slip-grid{padding:0.42rem 0.28rem 0.28rem;}
.slip-row{
  display:grid;grid-template-columns:minmax(0,1fr) 72px minmax(0,1fr);
  align-items:center;padding:0.4rem 0.42rem;border-bottom:1px solid rgba(255,255,255,0.04);
}
.slip-row:last-child{border-bottom:none;}
.slip-row.dial-row{background:rgba(255,209,102,0.07);border-bottom:1px solid var(--gold-dim);}
.slip-row.et-row{background:linear-gradient(90deg, rgba(255,48,79,0.08), rgba(51,87,255,0.08));}
.slip-row.mov-row{border-top:1px solid var(--gold-dim);background:rgba(255,209,102,0.07);}
.slip-cell{
  display:block;font-family:var(--mono);font-size:1.13rem;font-weight:800;min-width:0;
}
.slip-cell.left,.slip-cell.right{text-align:center;}
.slip-cell.speed-sub{font-size:0.76rem;color:var(--dim);font-weight:500;margin-top:0.08rem;}
.slip-lbl{
  text-align:center;font-size:0.63rem;color:var(--dim);text-transform:uppercase;
  letter-spacing:0.18em;padding:0;white-space:nowrap;
}
.slip-cell.highlight{color:var(--gold);animation:pulse 0.55s ease;}
.slip-cell.gold{color:var(--gold);}
.slip-cell.et-val{font-size:1.28rem;}
@keyframes pulse{0%{background:rgba(255,209,102,0.16);border-radius:8px;}100%{background:transparent;}}
.no-race{
  text-align:center;padding:2.3rem 1rem;border-radius:24px;color:#dbe4ff;
  background:linear-gradient(180deg, rgba(13,21,43,0.9), rgba(8,13,28,0.96));
  border:1px solid rgba(255,255,255,0.09);box-shadow:var(--shadow);
}
.no-race p{font-size:1rem;font-weight:700;letter-spacing:0.04em;margin-bottom:0.32rem;}
.no-race::after{
  content:'Awaiting next pair';display:block;margin-top:0.5rem;color:var(--dim);font-size:0.72rem;letter-spacing:0.18em;text-transform:uppercase;
}
.no-race .dot{display:inline-block;animation:blink 1.4s infinite;}
@keyframes blink{0%,100%{opacity:0.25;}50%{opacity:1;}}

/* ── Feed ── */
.feed-wrap{
  position:relative;background:linear-gradient(180deg, rgba(13,21,43,0.92), rgba(8,13,28,0.98));
  border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:0.7rem;
  min-height:40vh;display:flex;flex-direction:column;box-shadow:var(--shadow);
}
.feed-wrap::before{
  content:'';position:absolute;left:0;right:0;top:0;height:3px;
  background:linear-gradient(90deg,var(--blue),var(--gold));
}
.feed-toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;gap:0.5rem;}
.feed-toolbar span{font-size:0.68rem;color:var(--dim);letter-spacing:0.08em;text-transform:uppercase;}
.feed-scroll{
  max-height:52vh;overflow-y:auto;font-family:var(--mono);font-size:0.74rem;line-height:1.55;
  padding:0.35rem;border-radius:14px;background:rgba(255,255,255,0.03);
  -webkit-overflow-scrolling:touch;overscroll-behavior:contain;
}
.feed-line{color:var(--text);padding:0.12rem 0.2rem;border-radius:8px;}
.feed-time{color:#7d8ac8;margin-right:0.45rem;}
.feed-empty{color:var(--dim);padding:2rem 1rem;text-align:center;}
.feed-btn{
  background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);color:var(--dim);
  padding:0.35rem 0.7rem;border-radius:999px;cursor:pointer;font-size:0.66rem;font-weight:800;letter-spacing:0.08em;text-transform:uppercase;
}
.feed-btn:hover{border-color:var(--gold);color:var(--gold);}

/* ── Results ── */
.result-card{
  position:relative;background:linear-gradient(180deg, rgba(13,21,43,0.92), rgba(8,13,28,0.98));
  border:1px solid rgba(255,255,255,0.1);border-radius:18px;padding:0.8rem;margin-bottom:0.65rem;box-shadow:var(--shadow);
}
.result-card::before{
  content:'';position:absolute;left:0;right:0;top:0;height:3px;
  background:linear-gradient(90deg,var(--gold),var(--accent));
}
.result-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:0.7rem;gap:0.5rem;}
.result-cat{font-family:var(--display);font-size:0.96rem;font-weight:900;color:#fff;letter-spacing:0.08em;text-transform:uppercase;}
.result-time{font-size:0.64rem;color:var(--dim);letter-spacing:0.08em;text-transform:uppercase;}
.result-lanes{display:grid;grid-template-columns:1fr 1fr;gap:0.45rem;}
.result-lane{padding:0.5rem;background:rgba(255,255,255,0.04);border-radius:14px;border:1px solid rgba(255,255,255,0.05);}
.result-lane.winner{background:linear-gradient(180deg, rgba(255,209,102,0.12), rgba(255,209,102,0.06));border:1px solid var(--gold-dim);}
.result-driver{font-weight:700;font-size:0.8rem;margin-bottom:0.18rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.result-et{font-family:var(--mono);font-size:0.98rem;font-weight:800;}
.result-rt,.result-speed{font-family:var(--mono);font-size:0.68rem;color:var(--dim);}
.no-results{
  text-align:center;padding:2.5rem 1rem;color:var(--dim);border-radius:20px;
  background:rgba(255,255,255,0.03);border:1px dashed rgba(255,255,255,0.12);
}

/* ── Kiosk Settings ── */
.setting-card{
  position:relative;background:linear-gradient(180deg, rgba(13,21,43,0.92), rgba(8,13,28,0.98));
  border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:0.8rem;margin-bottom:0.65rem;box-shadow:var(--shadow);
}
.setting-card::before{
  content:'';position:absolute;left:0;right:0;top:0;height:3px;
  background:linear-gradient(90deg,var(--accent),var(--blue));
}
.setting-title{
  font-family:var(--display);font-size:0.9rem;color:#fff;text-transform:uppercase;
  letter-spacing:0.12em;font-weight:900;margin-bottom:0.72rem;
}
.setting-row{
  display:flex;justify-content:space-between;align-items:center;gap:0.75rem;padding:0.4rem 0;
  border-bottom:1px solid rgba(255,255,255,0.05);
}
.setting-row:last-child{border-bottom:none;padding-bottom:0;}
.setting-label{font-size:0.7rem;color:var(--dim);text-transform:uppercase;letter-spacing:0.12em;}
.setting-value{font-size:0.73rem;font-weight:700;color:#fff;text-align:right;}
.k-input{
  width:100%;padding:0.72rem 0.78rem;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.12);
  border-radius:14px;color:var(--text);font-size:0.9rem;font-family:var(--sans);
}
.k-input:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(255,209,102,0.12);}
.k-label{
  display:block;font-size:0.6rem;color:var(--dim);margin:0.55rem 0 0.25rem;
  text-transform:uppercase;letter-spacing:0.16em;font-weight:800;
}
.k-btn{
  display:block;width:100%;padding:0.82rem 0.9rem;margin-top:0.7rem;
  background:linear-gradient(135deg,var(--accent),#ff6a5f);color:#fff;border:none;border-radius:16px;
  font-size:0.78rem;font-weight:900;cursor:pointer;text-transform:uppercase;letter-spacing:0.12em;
  box-shadow:0 16px 32px rgba(255,48,79,0.22);
}
.k-btn:hover{filter:brightness(1.05);}
.k-btn-secondary{
  background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.12);color:#e7ecff;box-shadow:none;
}
.k-btn-secondary:hover{border-color:var(--gold);color:var(--gold);}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:0.35rem;vertical-align:middle;box-shadow:0 0 10px currentColor;}
.status-dot.on{background:var(--green);color:var(--green);}
.status-dot.off{background:var(--red);color:var(--red);}
.k-toast{
  position:fixed;top:1rem;left:50%;transform:translateX(-50%);
  background:linear-gradient(135deg,var(--gold),#fff0b0);color:#111;padding:0.6rem 1.3rem;border-radius:999px;
  font-weight:900;font-size:0.76rem;z-index:200;opacity:0;transition:opacity 0.3s;pointer-events:none;
  box-shadow:0 18px 35px rgba(0,0,0,0.35);
}
.k-toast.show{opacity:1;}

@media(max-width:600px){
  .header-logo{width:82px;}
  .app-header h1{font-size:1.25rem;}
  .hero-status{grid-template-columns:1fr;gap:0.35rem;}
  .hero-pill{padding:0.45rem 0.55rem;}
  .tab-content{padding:0.55rem;}
  .result-lanes{grid-template-columns:1fr;}
  .slip-row{grid-template-columns:minmax(0,1fr) 62px minmax(0,1fr);}
}
@media(max-height:600px){
  #splash{animation-delay:4.6s;}
  #app{animation-delay:4.95s;}
  .splash-inner{padding-top:1.35rem;}
  .splash-logo-frame{margin-bottom:0.9rem;}
  .splash-wordmark{font-size:clamp(2.15rem,8.8vw,3.5rem);}
  .header-top{gap:0.6rem;}
  .header-logo{width:70px;}
  .app-header{padding:0.62rem 0.72rem 0.58rem;}
  .app-header h1{font-size:1.1rem;}
  .app-header p{font-size:0.64rem;}
  .hero-status{margin-top:0.55rem;grid-template-columns:repeat(3,minmax(0,1fr));}
  .hero-pill{padding:0.38rem 0.42rem;border-radius:12px;}
  .hero-pill-label{font-size:0.48rem;}
  .hero-pill-value{font-size:0.58rem;}
  .tab-btn{min-height:54px;font-size:0.56rem;}
  .tab-btn svg{width:18px;height:18px;}
  .race-category{font-size:1.05rem;}
  .slip-driver{padding:0.45rem 0.55rem;}
  .slip-driver-name{font-size:0.82rem;}
  .slip-row{padding:0.28rem 0.3rem;}
  .slip-cell{font-size:0.95rem;}
  .slip-cell.et-val{font-size:1.08rem;}
  .slip-lbl{font-size:0.55rem;}
  .feed-wrap{height:calc(100dvh - 235px);}
}

/* Virtual Keyboard */
#vkbd{
  position:fixed;bottom:-300px;left:0;width:100%;background:#111;border-top:1px solid var(--border);
  padding:0.5rem;transition:bottom 0.3s cubic-bezier(0.4, 0, 0.2, 1);z-index:9999;box-shadow:0 -5px 15px rgba(0,0,0,0.5);
  display:flex;flex-direction:column;gap:0.3rem;
}
#vkbd.show{bottom:0;}
.vk-row{display:flex;justify-content:center;gap:0.3rem;}
.vk-key{
  flex:1;min-width:35px;max-width:55px;height:45px;background:#333;color:#fff;
  border:1px solid #444;border-radius:6px;font-size:1.2rem;font-weight:600;
  display:flex;align-items:center;justify-content:center;cursor:pointer;
  user-select:none;font-family:var(--sans);
}
.vk-key:active{background:var(--gold);color:#000;}
.vk-key.wide{max-width:70px;font-size:0.9rem;}
.vk-key.space{max-width:300px;font-size:0.9rem;}
.vk-close{
  position:absolute;top:-45px;right:10px;background:#333;color:#fff;
  padding:0.5rem 1rem;border-radius:6px;border:1px solid var(--border);font-size:0.8rem;
  font-family:var(--sans);font-weight:600;cursor:pointer;box-shadow:0 4px 6px rgba(0,0,0,0.3);
}
</style>
</head><body>

<div id="splash">
  <div class="splash-inner">
    <div class="splash-badge">Championship Data Stream</div>
    <div class="splash-logo-frame">
      <img src="/assets/nhra-logo" alt="NHRA Championship Drag Racing logo" class="splash-logo">
    </div>
    <div class="splash-wordmark">DARK <strong>MAWSON</strong></div>
    <div class="splash-sub">Race Day Timing Control</div>
    <div class="splash-track"><span></span><span></span><span></span></div>
  </div>
</div>

<div id="app">
  <header class="app-header">
    <div class="header-top">
      <img src="/assets/nhra-logo" alt="NHRA logo" class="header-logo">
      <div class="header-copy">
        <span class="header-kicker">Dark Mawson Timing Network</span>
        <h1 id="hdr-title">Timing Talk</h1>
        <p id="hdr-sub">{{ track_name or promoter or '' }}{% if event_name %}{% if track_name or promoter %} &middot; {% endif %}{{ event_name }}{% endif %}</p>
      </div>
      <div class="header-actions">
        <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()">
          Theme
          <small id="theme-toggle-label">Dark</small>
        </button>
      </div>
    </div>
    <div class="hero-status">
      <div class="hero-pill">
        <span class="hero-pill-label">Serial</span>
        <span class="hero-pill-value"><span class="status-dot {{ 'on' if 'Listen' in serial_status else 'off' }}"></span>{{ serial_status }}</span>
      </div>
      <div class="hero-pill">
        <span class="hero-pill-label">TCP</span>
        <span class="hero-pill-value"><span class="status-dot {{ 'on' if 'Listen' in tcp_status or 'conn' in tcp_status|lower else 'off' }}"></span>{{ tcp_status or 'Disabled' }}</span>
      </div>
      <div class="hero-pill">
        <span class="hero-pill-label">Input</span>
        <span class="hero-pill-value">{{ input_mode|upper }}</span>
      </div>
    </div>
  </header>

  <div class="tab-content">
    <!-- LIVE TAB -->
    <div id="tab-live" class="tab-panel active">
      <div class="no-race" id="no-race">
        <p>Waiting for race data<span class="dot">...</span></p>
      </div>
      <div id="race-active" class="slip" style="display:none;">
        <div class="slip-header">
          <div class="race-category" id="race-cat"></div>
          <div class="race-round" id="race-rnd"></div>
        </div>
        <div class="slip-drivers">
          <div class="slip-driver left">
            <div class="slip-driver-name" id="left-name">&mdash;</div>
            <div class="slip-driver-meta">
              <span class="car-num" id="left-num"></span>
              <span class="class-badge" id="left-class"></span>
            </div>
            <div id="left-winner" style="display:none;"><span class="slip-winner-tag">Winner</span></div>
          </div>
          <div class="slip-vs">VS</div>
          <div class="slip-driver right">
            <div class="slip-driver-name" id="right-name">&mdash;</div>
            <div class="slip-driver-meta">
              <span class="class-badge" id="right-class"></span>
              <span class="car-num" id="right-num"></span>
            </div>
            <div id="right-winner" style="display:none;"><span class="slip-winner-tag">Winner</span></div>
          </div>
        </div>
        <div class="slip-grid">
          <div class="slip-row dial-row" id="dial-row">
            <div><span class="slip-cell left gold" id="left-dial">&mdash;</span></div>
            <div class="slip-lbl">Dial-in</div>
            <div><span class="slip-cell right gold" id="right-dial">&mdash;</span></div>
          </div>
          <div class="slip-row">
            <div><span class="slip-cell left" id="left-rt">&mdash;</span></div>
            <div class="slip-lbl">R/T</div>
            <div><span class="slip-cell right" id="right-rt">&mdash;</span></div>
          </div>
          <div class="slip-row">
            <div><span class="slip-cell left" id="left-sixty">&mdash;</span></div>
            <div class="slip-lbl">60 ft</div>
            <div><span class="slip-cell right" id="right-sixty">&mdash;</span></div>
          </div>
          <div class="slip-row">
            <div><span class="slip-cell left" id="left-330">&mdash;</span></div>
            <div class="slip-lbl">330 ft</div>
            <div><span class="slip-cell right" id="right-330">&mdash;</span></div>
          </div>
          <div class="slip-row">
            <div>
              <span class="slip-cell left" id="left-660">&mdash;</span>
              <div class="slip-cell left speed-sub" id="left-660s"></div>
            </div>
            <div class="slip-lbl">660 ft</div>
            <div>
              <span class="slip-cell right" id="right-660">&mdash;</span>
              <div class="slip-cell right speed-sub" id="right-660s"></div>
            </div>
          </div>
          <div class="slip-row">
            <div><span class="slip-cell left" id="left-1000">&mdash;</span></div>
            <div class="slip-lbl">1000 ft</div>
            <div><span class="slip-cell right" id="right-1000">&mdash;</span></div>
          </div>
          <div class="slip-row et-row">
            <div><span class="slip-cell left et-val" id="left-et">&mdash;</span></div>
            <div class="slip-lbl">E.T.</div>
            <div><span class="slip-cell right et-val" id="right-et">&mdash;</span></div>
          </div>
          <div class="slip-row">
            <div><span class="slip-cell left" id="left-mph">&mdash;</span></div>
            <div class="slip-lbl">MPH</div>
            <div><span class="slip-cell right" id="right-mph">&mdash;</span></div>
          </div>
          <div class="slip-row mov-row" id="mov-row" style="display:none;">
            <div><span class="slip-cell left gold" id="left-mov"></span></div>
            <div class="slip-lbl">MOV</div>
            <div><span class="slip-cell right gold" id="right-mov"></span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- FEED TAB -->
    <div id="tab-feed" class="tab-panel">
      <div class="feed-wrap">
        <div class="feed-toolbar">
          <span id="feed-count">0 lines</span>
          <div>
            <label style="font-size:0.65rem;color:var(--dim);margin-right:0.4rem;"><input type="checkbox" id="feed-auto" checked> Auto-scroll</label>
            <button class="feed-btn" onclick="clearFeed()">Clear</button>
          </div>
        </div>
        <div class="feed-scroll" id="feed">
          <div class="feed-empty">Waiting for data...</div>
        </div>
      </div>
    </div>

    <!-- RESULTS TAB -->
    <div id="tab-results" class="tab-panel">
      <div id="results-list">
        <div class="no-results">No completed races yet</div>
      </div>
    </div>

    <!-- SETTINGS TAB -->
    <div id="tab-settings" class="tab-panel">
      <div id="setup-banner" style="display:{{ 'block' if needs_setup else 'none' }};background:rgba(212,160,23,0.1);border:1px solid var(--gold-dim);border-radius:10px;padding:0.75rem;margin-bottom:0.75rem;text-align:center;">
        <p style="color:var(--gold);font-weight:700;margin-bottom:0.2rem;">Setup Required</p>
        <p style="color:var(--dim);font-size:0.75rem;">Enter a track name or promoter name to get started.</p>
      </div>

      <div class="setting-card">
        <div class="setting-title">Event Info</div>
        <label class="k-label">Track Name</label>
        <input class="k-input" id="s-track" value="{{ track_name or '' }}">
        <label class="k-label">Event Name</label>
        <input class="k-input" id="s-event" value="{{ event_name or '' }}">
        <label class="k-label">Promoter</label>
        <input class="k-input" id="s-promoter" value="{{ promoter or '' }}">
        <button class="k-btn" onclick="saveEvent()">Save Event Info</button>
      </div>

      <div class="setting-card">
        <div class="setting-title">Connection Status</div>
        <div class="setting-row">
          <span class="setting-label">IP Address</span>
          <span class="setting-value" id="s-ip">{{ wifi_ip or '&mdash;' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Tailscale IP</span>
          <span class="setting-value" id="s-ts-ip">{{ tailscale_ip or '&mdash;' }}</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Serial</span>
          <span class="setting-value"><span class="status-dot {{ 'on' if 'Listen' in serial_status else 'off' }}"></span><span id="s-serial">{{ serial_status }}</span></span>
        </div>
        <div class="setting-row">
          <span class="setting-label">TCP/IP</span>
          <span class="setting-value"><span class="status-dot {{ 'on' if 'Listen' in tcp_status or 'conn' in tcp_status|lower else 'off' }}"></span><span id="s-tcp">{{ tcp_status or 'Disabled' }}</span></span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Input Mode</span>
          <span class="setting-value" id="s-mode">{{ input_mode }}</span>
        </div>
      </div>
      <div class="setting-card">
        <div class="setting-title">Display Theme</div>
        <div class="theme-switcher">
          <button class="theme-choice" id="theme-choice-dark" onclick="setTheme('dark')">Dark<small>Race night</small></button>
          <button class="theme-choice" id="theme-choice-light" onclick="setTheme('light')">Light<small>Day mode</small></button>
        </div>
      </div>


      <div class="setting-card">
        <div class="setting-title">Test Signals</div>
        <div style="display:flex;gap:0.4rem;flex-wrap:wrap;">
          <button class="k-btn k-btn-secondary" style="flex:1;" onclick="sendTest('test-serial')">Test Serial</button>
          <button class="k-btn k-btn-secondary" style="flex:1;" onclick="sendTest('test-xml')">Test TCP</button>
          <button class="k-btn k-btn-secondary" style="flex:1;" onclick="sendTest('test-api')">Test Firebase</button>
        </div>
      </div>

      <div class="setting-card">
        <div class="setting-title">System</div>
        <div class="setting-row">
          <span class="setting-label">Uptime</span>
          <span class="setting-value" id="s-uptime">&mdash;</span>
        </div>
        <div class="setting-row">
          <span class="setting-label">Lines Received</span>
          <span class="setting-value" id="s-lines">0</span>
        </div>
        <button class="k-btn k-btn-secondary" onclick="if(confirm('Restart the timing service?'))sendTest('restart')" style="border-color:var(--red);color:var(--red);">Restart Service</button>
      </div>

      <p style="text-align:center;color:var(--dim);font-size:0.65rem;margin-top:0.75rem;">
        <a href="/settings" style="color:var(--gold);">Open Full Settings</a>
      </p>
    </div>
  </div>

  <nav class="tab-bar">
    <button class="tab-btn active" data-tab="live" onclick="switchTab('live')">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 3v18h2v-7h10l-2-4 2-4H7V3z"/></svg>
      Live
    </button>
    <button class="tab-btn" data-tab="feed" onclick="switchTab('feed')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      Feed
    </button>
    <button class="tab-btn" data-tab="results" onclick="switchTab('results')">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 5h-2V3H7v2H5c-1.1 0-2 .9-2 2v1c0 2.55 1.92 4.63 4.39 4.94.63 1.5 1.98 2.63 3.61 2.96V19H7v2h10v-2h-4v-3.1c1.63-.33 2.98-1.46 3.61-2.96C19.08 12.63 21 10.55 21 8V7c0-1.1-.9-2-2-2zM5 8V7h2v3.82C5.84 10.4 5 9.3 5 8zm14 0c0 1.3-.84 2.4-2 2.82V7h2v1z"/></svg>
      Results
    </button>
    <button class="tab-btn" data-tab="settings" onclick="switchTab('settings')">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>
      Settings
    </button>
  </nav>
</div>

<!-- Virtual Keyboard -->
<div id="vkbd">
  <div class="vk-close" onclick="hideKbd()">Done</div>
  <div class="vk-row" id="vk-row-1"></div>
  <div class="vk-row" id="vk-row-2"></div>
  <div class="vk-row" id="vk-row-3"></div>
  <div class="vk-row" id="vk-row-4"></div>
</div>

<div class="k-toast" id="toast"></div>

<script>
var needsSetup = {{ 'true' if needs_setup else 'false' }};
var feedCount = 0;
var activeTab = 'live';
var currentTheme = localStorage.getItem('timing-kiosk-theme') || 'dark';

function applyTheme(theme) {
  currentTheme = theme;
  document.body.setAttribute('data-theme', theme);
  var toggleLabel = document.getElementById('theme-toggle-label');
  if (toggleLabel) toggleLabel.textContent = theme === 'light' ? 'Light' : 'Dark';
  var darkBtn = document.getElementById('theme-choice-dark');
  var lightBtn = document.getElementById('theme-choice-light');
  if (darkBtn) darkBtn.classList.toggle('active', theme === 'dark');
  if (lightBtn) lightBtn.classList.toggle('active', theme === 'light');
}

function setTheme(theme) {
  localStorage.setItem('timing-kiosk-theme', theme);
  applyTheme(theme);
  toast('Theme updated');
}

function toggleTheme() {
  setTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

applyTheme(currentTheme);

if (needsSetup) {
  document.getElementById('splash').style.animation = 'splashOut 0.75s ease 2.6s forwards';
  document.getElementById('app').style.animation = 'fadeIn 0.65s ease 2.95s forwards';
  setTimeout(function() { switchTab('settings'); }, 3400);
}

var tabOrder = ['live', 'feed', 'results', 'settings'];

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector('[data-tab="' + name + '"]').classList.add('active');
  var scroller = document.querySelector('.tab-content');
  window.scrollTo(0, 0);
}

function switchTabByOffset(offset) {
  var currentIndex = tabOrder.indexOf(activeTab);
  if (currentIndex === -1) currentIndex = 0;
  var nextIndex = Math.max(0, Math.min(tabOrder.length - 1, currentIndex + offset));
  if (nextIndex !== currentIndex) switchTab(tabOrder[nextIndex]);
}

function toast(msg) {
  var el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(function() { el.classList.remove('show'); }, 2000);
}

function updateRace() {
  fetch('/api/race-state').then(function(r){return r.json();}).then(function(d) {
    var race = d.current;
    var hasData = race.left.name || race.right.name || race.category;
    document.getElementById('no-race').style.display = hasData ? 'none' : 'block';
    document.getElementById('race-active').style.display = hasData ? 'block' : 'none';
    if (hasData) {
      document.getElementById('race-cat').textContent = race.category || 'RACE';
      document.getElementById('race-rnd').textContent = race.round || '';
      updateSlip('left', race.left, race.winner);
      updateSlip('right', race.right, race.winner);
      var hasDial = race.left.dial_in || race.right.dial_in;
      document.getElementById('dial-row').style.display = hasDial ? 'grid' : 'none';
      var hasMov = race.left.mov || race.right.mov;
      document.getElementById('mov-row').style.display = hasMov ? 'grid' : 'none';
    }
    if (d.completed && d.completed.length > 0) renderResults(d.completed);
  }).catch(function(){});
}

function updateSlip(side, data, winner) {
  var isWinner = winner && winner.toUpperCase() === side.toUpperCase();
  setVal(side + '-name', data.name);
  var numEl = document.getElementById(side + '-num');
  if (numEl) numEl.textContent = (data.car_num || data.number) ? '#' + (data.car_num || data.number) : '';
  var clsEl = document.getElementById(side + '-class');
  if (clsEl) { clsEl.textContent = data.class_name || ''; clsEl.style.display = data.class_name ? 'inline' : 'none'; }
  setVal(side + '-dial', data.dial_in);
  animateVal(side + '-rt', data.rt);
  animateVal(side + '-sixty', data.sixty);
  animateVal(side + '-330', data.three30);
  animateVal(side + '-660', data.six60);
  var s660 = document.getElementById(side + '-660s');
  if (s660) s660.textContent = data.six60_speed ? data.six60_speed + ' mph' : '';
  animateVal(side + '-1000', data.thousand);
  animateVal(side + '-et', data.et);
  animateVal(side + '-mph', data.speed);
  setVal(side + '-mov', data.mov);
  document.getElementById(side + '-winner').style.display = isWinner ? 'block' : 'none';
}

function setVal(id, val) {
  var el = document.getElementById(id);
  if (el) el.textContent = val || '\\u2014';
}

function animateVal(id, val) {
  var el = document.getElementById(id);
  if (!el) return;
  var display = val || '\\u2014';
  if (el.textContent !== display) {
    el.textContent = display;
    if (val) { el.classList.add('highlight'); setTimeout(function(){ el.classList.remove('highlight'); }, 600); }
  }
}

function renderResults(completed) {
  var el = document.getElementById('results-list');
  if (!completed.length) { el.innerHTML = '<div class="no-results">No completed races yet</div>'; return; }
  var html = '';
  for (var i = completed.length - 1; i >= 0; i--) {
    var r = completed[i];
    var lw = r.winner && r.winner.toUpperCase() === 'LEFT';
    var rw = r.winner && r.winner.toUpperCase() === 'RIGHT';
    html += '<div class="result-card"><div class="result-header"><span class="result-cat">' + esc(r.category || 'RACE') + '</span><span class="result-time">' + esc(r.timestamp || '') + '</span></div>';
    html += '<div class="result-lanes">';
    html += resultLane(r.left, lw);
    html += resultLane(r.right, rw);
    html += '</div></div>';
  }
  el.innerHTML = html;
}

function pollFeed() {
  fetch('/api/data?since=' + feedCount).then(function(r){return r.json();}).then(function(d) {
    if (d.lines.length > 0) {
      var feed = document.getElementById('feed');
      if (feedCount === 0) feed.innerHTML = '';
      d.lines.forEach(function(item) {
        var div = document.createElement('div');
        div.className = 'feed-line';
        div.innerHTML = '<span class="feed-time">' + esc(item.time) + '</span>' + esc(item.data);
        feed.appendChild(div);
      });
      feedCount += d.lines.length;
      document.getElementById('feed-count').textContent = feedCount + ' lines';
      if (document.getElementById('feed-auto').checked) feed.scrollTop = feed.scrollHeight;
    }
  }).catch(function(){});
}

function clearFeed() {
  fetch('/api/clear-data', {method:'POST'}).then(function() {
    document.getElementById('feed').innerHTML = '<div class="feed-empty">Waiting for data...</div>';
    feedCount = 0;
    document.getElementById('feed-count').textContent = '0 lines';
  });
}

function esc(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

function resultLane(lane, isWin) {
  var cls = isWin ? ' winner' : '';
  var h = '<div class="result-lane' + cls + '">';
  h += '<div class="result-driver">' + esc(lane.name || '\\u2014') + (isWin ? ' \\u2605' : '') + '</div>';
  if (lane.class_name) h += '<div style="font-size:0.6rem;color:var(--dim);margin-bottom:0.1rem;">' + esc(lane.class_name) + '</div>';
  if (lane.dial_in) h += '<div style="font-size:0.7rem;color:var(--gold);margin-bottom:0.15rem;">Dial: ' + esc(lane.dial_in) + '</div>';
  h += '<div class="result-et">' + esc(lane.et || '\\u2014') + '</div>';
  h += '<div class="result-rt">RT: ' + esc(lane.rt || '\\u2014') + '</div>';
  h += '<div class="result-speed">' + (lane.speed ? esc(lane.speed) + ' mph' : '') + '</div>';
  if (lane.mov) h += '<div style="font-size:0.7rem;color:var(--gold);margin-top:0.1rem;">MOV: ' + esc(lane.mov) + '</div>';
  h += '</div>';
  return h;
}

function pollStatus() {
  fetch('/api/status').then(function(r){return r.json();}).then(function(d) {
    document.getElementById('s-uptime').textContent = d.uptime || '\\u2014';
    document.getElementById('s-lines').textContent = d.lines_received || 0;
    document.getElementById('s-serial').textContent = d.serial_status || '\\u2014';
    document.getElementById('s-tcp').textContent = d.tcp_status || 'Disabled';
    document.getElementById('s-mode').textContent = d.input_mode || '\\u2014';
    document.getElementById('s-ip').textContent = d.wifi_ip || '\\u2014';
    document.getElementById('s-ts-ip').textContent = d.tailscale_ip || '\\u2014';
  }).catch(function(){});
}

function saveEvent() {
  var data = {
    track: document.getElementById('s-track').value,
    race: document.getElementById('s-event').value,
    promoter: document.getElementById('s-promoter').value
  };
  fetch('/api/settings-json', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  }).then(function(r){return r.json();}).then(function(d) {
    if (d.ok) {
      document.getElementById('hdr-title').textContent = 'Timing Talk';
      var sub = data.track || data.promoter || '';
      if (data.race) sub += (sub ? ' \u00b7 ' : '') + data.race;
      document.getElementById('hdr-sub').textContent = sub;
      document.getElementById('setup-banner').style.display = 'none';
      toast('Settings saved');
    }
  }).catch(function(){ toast('Failed to save'); });
}

function sendTest(action) {
  fetch('/settings/' + action, {method:'POST'}).then(function(){ toast('Test sent'); }).catch(function(){});
}

setInterval(updateRace, 1000);
setInterval(pollFeed, 1000);
setInterval(pollStatus, 5000);
updateRace(); pollFeed(); pollStatus();

/* Virtual Keyboard Logic */
var activeInput = null;
var isShift = false;
var keys = [
  ['1','2','3','4','5','6','7','8','9','0'],
  ['q','w','e','r','t','y','u','i','o','p'],
  ['a','s','d','f','g','h','j','k','l'],
  ['Shift','z','x','c','v','b','n','m','Bksp']
];

function renderKbd() {
  for(var i=0; i<4; i++) {
    var row = document.getElementById('vk-row-'+(i+1));
    row.innerHTML = '';
    keys[i].forEach(function(k) {
      var btn = document.createElement('div');
      btn.className = 'vk-key' + (k.length>1 ? ' wide' : '');
      var display = k;
      if(k.length === 1) display = isShift ? k.toUpperCase() : k.toLowerCase();
      btn.textContent = display;
      btn.onmousedown = function(e) {
        e.preventDefault();
        if(k === 'Shift') { isShift = !isShift; renderKbd(); return; }
        if(!activeInput) return;
        if(k === 'Bksp') {
          activeInput.value = activeInput.value.slice(0, -1);
        } else {
          activeInput.value += display;
        }
        if(isShift && k.length === 1) { isShift = false; renderKbd(); }
      };
      row.appendChild(btn);
    });
  }
  var spaceRow = document.getElementById('vk-row-4');
  var space = document.createElement('div');
  space.className = 'vk-key space';
  space.textContent = 'Space';
  space.onmousedown = function(e) { e.preventDefault(); if(activeInput) activeInput.value += ' '; };
  spaceRow.appendChild(space);
}
renderKbd();

function showKbd(el) { activeInput = el; document.getElementById('vkbd').classList.add('show'); }
function hideKbd() { document.getElementById('vkbd').classList.remove('show'); activeInput = null; }

document.querySelectorAll('.k-input').forEach(function(el) {
  el.addEventListener('focus', function() { showKbd(this); });
  // Prevent system keyboard from popping up on touch devices if possible
  el.setAttribute('readonly', 'readonly');
  el.addEventListener('click', function() { this.removeAttribute('readonly'); showKbd(this); setTimeout(()=>this.setAttribute('readonly', 'readonly'), 100); });
});

/* Swipe navigation disabled while touch scrolling is tuned. */
</script>
</body></html>"""


# ══════════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════════

def _uptime():
    secs = int(time.time() - _status["uptime_start"])
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


@app.route("/")
def index():
    if _mode == "setup":
        networks = wifi_manager.scan_networks()
        return render_template_string(SETUP_HTML, networks=networks)
    else:
        with _status_lock:
            st = dict(_status)
        return render_template_string(DASHBOARD_HTML, status=st, uptime=_uptime())


@app.route("/data")
def data_page():
    return render_template_string(DATA_HTML, data=list(_data_buffer)[-100:])


@app.route("/wifi")
def wifi_page():
    networks = wifi_manager.scan_networks()
    return render_template_string(
        WIFI_HTML,
        networks=networks,
        current_ssid=wifi_manager.get_ssid(),
        current_ip=wifi_manager.get_ip(),
    )


@app.route("/connect", methods=["POST"])
def do_connect():
    ssid = request.form.get("ssid", "")
    password = request.form.get("password", "")
    from_page = request.form.get("from", "")

    success = wifi_manager.connect(ssid, password)
    ip = wifi_manager.get_ip() if success else None

    if success:
        update_status(wifi_ssid=ssid, wifi_ip=ip)

    if _mode == "setup":
        if success and _on_connected:
            threading.Thread(target=_on_connected, daemon=True).start()
        return render_template_string(SETUP_RESULT_HTML, success=success, ssid=ssid, ip=ip)
    else:
        if from_page == "wifi":
            return redirect("/wifi")
        return redirect("/")


@app.route("/settings")
def settings_page():
    import datetime
    from sender import scan_serial_ports
    ports = scan_serial_ports()
    device_cfg = get_device_config()
    with _status_lock:
        current_port = _status.get("serial_port")
        serial_status = _status.get("serial_status", "")
        tcp_status = _status.get("tcp_status", "Disabled")
        input_mode = _status.get("input_mode", "both")
        baud_rate = _status.get("baud_rate", 9600)
    message = request.args.get("msg")
    msg_ok = request.args.get("ok") == "1"
    today_date = datetime.datetime.now().strftime("%Y-%m-%d")
    return render_template_string(
        SETTINGS_HTML,
        ports=ports,
        current_port=current_port,
        serial_status=serial_status,
        tcp_status=tcp_status,
        tcp_host=device_cfg.get("tcp_host", ""),
        tcp_port=device_cfg.get("tcp_port", ""),
        secondary_url=device_cfg.get("secondary_url", ""),
        input_mode=input_mode,
        baud_rate=baud_rate,
        message=message,
        msg_ok=msg_ok,
        event=get_event_config(),
        today_date=today_date,
        kiosk_autostart=device_cfg.get("kiosk_autostart", False),
    )


@app.route("/settings/toggle-mode", methods=["POST"])
def toggle_mode():
    toggle_target = request.form.get("toggle")
    with _status_lock:
        current_mode = _status.get("input_mode", "both")

    new_mode = current_mode
    if toggle_target == "serial":
        if current_mode == "both": new_mode = "tcp"
        elif current_mode == "serial": new_mode = "none"
        elif current_mode == "tcp": new_mode = "both"
        elif current_mode == "none": new_mode = "serial"
    elif toggle_target == "tcp":
        if current_mode == "both": new_mode = "serial"
        elif current_mode == "tcp": new_mode = "none"
        elif current_mode == "serial": new_mode = "both"
        elif current_mode == "none": new_mode = "tcp"

    save_device_config(input_mode=new_mode)
    update_status(input_mode=new_mode)
    if _on_mode_change:
        _on_mode_change(new_mode)
    return redirect(f"/settings?msg=Connection+toggled&ok=1")


_on_secondary_url_change = None

def set_on_secondary_url_change(callback):
    global _on_secondary_url_change
    _on_secondary_url_change = callback


@app.route("/settings/event", methods=["POST"])
def save_event():
    import datetime
    track = request.form.get("track", "").strip()
    race = request.form.get("race", "").strip()
    promoter = request.form.get("promoter", "").strip()
    secondary_url = request.form.get("secondary_url", "").strip()

    if not track and not promoter:
        return redirect("/settings?msg=Track+or+Promoter+is+required&ok=0")

    if not race:
        race = datetime.datetime.now().strftime("%Y-%m-%d")

    cfg = {
        "track": track,
        "race": race,
        "promoter": promoter,
    }
    save_event_config(cfg)
    save_device_config(secondary_url=secondary_url)
    if _on_secondary_url_change:
        _on_secondary_url_change(secondary_url)
        
    return redirect("/settings?msg=Settings+saved&ok=1")


@app.route("/settings/port", methods=["POST"])
def change_port():
    new_port = request.form.get("port", "")
    if not new_port:
        return redirect("/settings?msg=No+port+selected&ok=0")
    save_device_config(port=new_port)
    if _on_port_change:
        _on_port_change(new_port)
        return redirect(f"/settings?msg=Switched+to+{new_port}+(saved)&ok=1")
    return redirect("/settings?msg=Port+change+not+available&ok=0")


@app.route("/settings/baud", methods=["POST"])
def change_baud():
    from sender import scan_serial_ports
    new_baud = int(request.form.get("baud", 9600))
    update_status(baud_rate=new_baud)
    save_device_config(baud=new_baud)
    if _on_port_change:
        with _status_lock:
            current_port = _status.get("serial_port")
        if current_port:
            _on_port_change(current_port, new_baud)
    return redirect(f"/settings?msg=Baud+rate+set+to+{new_baud}+(saved)&ok=1")


@app.route("/settings/tcp", methods=["POST"])
def change_tcp():
    new_host = request.form.get("tcp_host", "").strip()
    new_port = request.form.get("tcp_port", "").strip()
    if not new_host and not new_port:
        save_device_config(tcp_host="", tcp_port="")
        if _on_tcp_change:
            _on_tcp_change("", "")
        return redirect("/settings?msg=TCP+Input+Disabled&ok=1")

    if not new_host or not new_port:
        return redirect("/settings?msg=Both+Host+and+Port+are+required&ok=0")

    save_device_config(tcp_host=new_host, tcp_port=new_port)
    if _on_tcp_change:
        _on_tcp_change(new_host, new_port)
    return redirect(f"/settings?msg=TCP+configured+for+{new_host}:{new_port}&ok=1")


@app.route("/settings/test-api", methods=["POST"])
def test_api():
    """Send a test ping to the Firebase API."""
    from sender import send_to_api, DEFAULT_API
    with _status_lock:
        api_url = _status.get("api_url") or DEFAULT_API
    try:
        success = send_to_api(api_url, "TEST_PING")
        if success:
            return redirect("/settings?msg=API+connection+successful&ok=1")
        else:
            return redirect("/settings?msg=API+request+failed&ok=0")
    except Exception as e:
        return redirect(f"/settings?msg=API+error:+{e}&ok=0")


@app.route("/settings/test-serial", methods=["POST"])
def test_serial():
    """Inject a fake race through the full pipeline (OLED + Dashboard + Firebase)."""
    if _on_test_serial:
        _on_test_serial()
        return redirect("/settings?msg=Test+race+sent+—+check+OLED+and+dashboard&ok=1")

    from sender import TEST_RACE_DATA, send_or_queue, DEFAULT_API, _get_device_id
    with _status_lock:
        api_url = _status.get("api_url") or DEFAULT_API
        secondary_url = _status.get("secondary_url")

    batch_lines = []
    for line in TEST_RACE_DATA:
        push_data_line(f"[TEST] {line}")
        batch_lines.append(line)

    batch = "\n".join(batch_lines)
    threading.Thread(
        target=send_or_queue,
        args=(api_url, batch, _get_device_id(), secondary_url),
        daemon=True,
    ).start()

    return redirect("/settings?msg=Test+serial+race+sent&ok=1")


@app.route("/settings/test-xml", methods=["POST"])
def test_xml():
    """Send a test XML TimeSlip (TCP/IP format) to Firebase."""
    from sender import TEST_XML_DATA, send_or_queue, DEFAULT_API, _get_device_id
    with _status_lock:
        api_url = _status.get("api_url") or DEFAULT_API
        secondary_url = _status.get("secondary_url")

    push_data_line("[TEST-XML] Sending XML TimeSlip...")
    for line in TEST_XML_DATA.strip().split("\n"):
        push_data_line(f"[TEST-XML] {line.strip()}")

    threading.Thread(
        target=send_or_queue,
        args=(api_url, TEST_XML_DATA.strip(), _get_device_id(), secondary_url),
        daemon=True,
    ).start()

    return redirect("/settings?msg=Test+XML+TimeSlip+sent&ok=1")


@app.route("/api/log-stats")
def api_log_stats():
    from sender import get_data_logger, get_offline_queue
    stats = get_data_logger().get_log_stats()
    stats["queued"] = get_offline_queue().pending_count()
    return jsonify(stats)


@app.route("/kiosk")
def kiosk_page():
    event = get_event_config()
    with _status_lock:
        serial_status = _status.get("serial_status", "Not started")
        tcp_status = _status.get("tcp_status", "Disabled")
        input_mode = _status.get("input_mode", "both")
        wifi_ip = _status.get("wifi_ip", "")
        tailscale_ip = _status.get("tailscale_ip", "")
    needs_setup = not (event.get("track", "").strip() or event.get("promoter", "").strip())
    
    from flask import make_response
    response = make_response(render_template_string(
        KIOSK_HTML,
        track_name=event.get("track", ""),
        event_name=event.get("race", ""),
        promoter=event.get("promoter", ""),
        needs_setup=needs_setup,
        input_mode=input_mode,
        serial_status=serial_status,
        tcp_status=tcp_status,
        wifi_ip=wifi_ip,
        tailscale_ip=tailscale_ip,
    ))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route("/api/race-state")
def api_race_state():
    return jsonify({
        "current": _race_parser.get_state(),
        "completed": _race_parser.get_completed(),
    })


@app.route("/api/settings-json", methods=["GET", "POST"])
def api_settings_json():
    if request.method == "GET":
        event = get_event_config()
        device = get_device_config()
        with _status_lock:
            st = dict(_status)
        return jsonify({"event": event, "device": device, "status": st})

    data = request.get_json(silent=True) or {}
    track = data.get("track", "").strip()
    race = data.get("race", "").strip()
    promoter = data.get("promoter", "").strip()

    if track or promoter:
        import datetime
        if not race:
            race = datetime.datetime.now().strftime("%Y-%m-%d")
        save_event_config({"track": track, "race": race, "promoter": promoter})

    secondary_url = data.get("secondary_url")
    if secondary_url is not None:
        save_device_config(secondary_url=secondary_url.strip())
        if _on_secondary_url_change:
            _on_secondary_url_change(secondary_url.strip())

    return jsonify({"ok": True})


@app.route("/settings/kiosk-stop", methods=["POST"])
def kiosk_stop():
    stop_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stop-kiosk.sh")
    try:
        subprocess.run([stop_script], check=False)
        return redirect("/settings?msg=Kiosk+mode+closed.+Use+the+desktop+icon+to+start+it+again.&ok=1")
    except Exception as e:
        log.warning("Could not stop kiosk browser: %s", e)
        return redirect("/settings?msg=Could+not+close+kiosk+mode&ok=0")


@app.route("/settings/kiosk-autostart", methods=["POST"])
def kiosk_autostart():
    enabled = request.form.get("kiosk_autostart") == "1"
    save_device_config(kiosk_autostart=enabled)
    msg = "Kiosk+auto-start+enabled" if enabled else "Kiosk+auto-start+disabled"
    return redirect(f"/settings?msg={msg}&ok=1")


@app.route("/settings/upload-day", methods=["POST"])
def upload_day():
    """Send a full day's log file to Firebase in small batches (background)."""
    date_str = request.form.get("date", "").strip()
    if not date_str:
        return redirect("/settings?msg=No+date+selected&ok=0")
    from sender import upload_day_log, get_data_logger, DEFAULT_API, _get_device_id
    with _status_lock:
        api_url = _status.get("api_url") or DEFAULT_API
        secondary_url = _status.get("secondary_url")
    device_id = _get_device_id()
    log_dir = get_data_logger().get_log_stats().get("log_dir", "")
    filepath = os.path.join(log_dir, f"{date_str}.log")
    if not os.path.isfile(filepath):
        return redirect(f"/settings?msg=No+log+file+for+{date_str.replace('-', '+')}&ok=0")

    def do_upload():
        batches, lines, err = upload_day_log(date_str, api_url, device_id, batch_size=50, secondary_url=secondary_url)
        log.info("Upload day %s: %d batches, %d lines, err=%s", date_str, batches, lines, err)

    threading.Thread(target=do_upload, daemon=True).start()
    return redirect(f"/settings?msg=Uploading+{date_str}+in+background&ok=1")


@app.route("/settings/flush-queue", methods=["POST"])
def flush_queue():
    from sender import get_offline_queue, get_data_logger
    queue = get_offline_queue()
    pending = queue.pending_count()
    if pending == 0:
        cleaned = get_data_logger().cleanup_old_logs()
        msg = "No+queued+data"
        if cleaned > 0:
            msg += f",+cleaned+{cleaned}+old+log+files"
        return redirect(f"/settings?msg={msg}&ok=1")
    sent, failed = queue.flush()
    remaining = queue.pending_count()
    msg = f"Uploaded+{sent}+batches"
    if remaining == 0:
        cleaned = get_data_logger().cleanup_old_logs()
        if cleaned > 0:
            msg += f",+cleaned+{cleaned}+old+logs"
    elif remaining > 0:
        msg += f",+{remaining}+still+pending"
    return redirect(f"/settings?msg={msg}&ok={'1' if failed == 0 else '0'}")


@app.route("/settings/restart", methods=["POST"])
def restart_service():
    import subprocess
    subprocess.Popen(["sudo", "systemctl", "restart", "nhra-timing"], close_fds=True)
    return redirect("/settings?msg=Service+restarting...&ok=1")


# ── JSON APIs for live polling ──

@app.route("/api/status")
def api_status():
    with _status_lock:
        st = dict(_status)
    st["uptime"] = _uptime()
    return jsonify(st)


@app.route("/api/data")
def api_data():
    since = int(request.args.get("since", 0))
    all_data = list(_data_buffer)
    new_lines = all_data[since:] if since < len(all_data) else []
    return jsonify({"lines": new_lines, "total": len(all_data)})


@app.route("/api/clear-data", methods=["POST"])
def clear_data():
    _data_buffer.clear()
    return jsonify({"success": True})


# Captive portal detection — only redirect in setup mode
@app.route("/hotspot-detect.html")
@app.route("/library/test/success.html")
@app.route("/generate_204")
@app.route("/gen_204")
@app.route("/connecttest.txt")
@app.route("/ncsi.txt")
@app.route("/redirect")
@app.route("/success.txt")
def captive_redirect():
    if _mode == "setup":
        return redirect("/", code=302)
    return "", 204


def start(port=80):
    log.info("Starting web interface on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def start_background(port=80):
    t = threading.Thread(target=start, args=(port,), daemon=True)
    t.start()
    return t
