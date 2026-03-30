import os
import json
import re
import uuid

BASE_DIR = "/var/lib/timing-talk"
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "device_id": str(uuid.uuid4()),
    "device_name": "TimingTalk-Device",
    "track_name": "",
    "track_location": "",
    "event_name": "",
    "event_start_date": "",
    "event_end_date": "",
    "remote_password": "",
    "serial": {
        "port": "auto",
        "baud_rate": 9600,
        "byte_size": 8,
        "parity": "N",
        "stop_bits": 1,
        "timeout": 1,
    },
    "tcp": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 4000,
    },
    "logging": {
        "db_path": os.path.join(BASE_DIR, "data.db"),
        "raw_dir": os.path.join(BASE_DIR, "raw"),
        "max_raw_file_size_mb": 50,
        "retention_days": 60,
    },
    "cloud": {
        "firebase_credentials": os.path.join(BASE_DIR, "firebase-credentials.json"),
        "project_id": "nhra-timing-api",
        "database_url": "https://nhra-timing-api-default-rtdb.firebaseio.com",
        "sync_batch_size": 100,
        "heartbeat_interval_sec": 30,
        "support_poll_interval_sec": 5,
    },
    "network": {
        "ap_ssid_prefix": "TimingTalk",
        "ap_password": "timingtalk",
        "captive_portal_port": 80,
        "portal_port_client": 8080,
        "saved_wifi": [],
    },
    "oled": {
        "enabled": True,
        "i2c_port": 1,
        "i2c_address": 0x3C,
        "rotation": 0,
        "screen_cycle_sec": 5,
    },
    "test": {
        "enabled": False,
        "interval_sec": 3,
        "serial": True,
        "tcp": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    config = DEFAULTS.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            file_config = json.load(f)
        config = _deep_merge(config, file_config)
    return config


def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def ensure_dirs(config: dict):
    os.makedirs(config["logging"]["raw_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(config["logging"]["db_path"]), exist_ok=True)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:120]


def make_event_id(config: dict) -> str:
    """Derive a stable event ID from current config."""
    from datetime import datetime
    device_id = config.get("device_id", "unknown")[:8]
    event_name = (config.get("event_name") or "").strip()
    start_date = (config.get("event_start_date") or "").strip()
    today = datetime.now().strftime("%Y-%m-%d")
    date_key = start_date or today
    name_key = event_name or date_key
    return _slugify(f"{device_id}_{name_key}_{date_key}")


def effective_event_name(config: dict) -> str:
    """Return the display name for the current event."""
    from datetime import datetime
    event_name = (config.get("event_name") or "").strip()
    start_date = (config.get("event_start_date") or "").strip()
    today = datetime.now().strftime("%Y-%m-%d")
    if event_name and start_date:
        return event_name
    if event_name:
        return f"{event_name} - {today}"
    return today
