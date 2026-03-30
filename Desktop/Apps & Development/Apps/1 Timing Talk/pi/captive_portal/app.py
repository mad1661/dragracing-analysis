import logging
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, redirect, jsonify
from remote_support import support_snapshot

logger = logging.getLogger("timing-talk.portal")

app = Flask(__name__)

_network_manager = None
_config = None
_save_config_fn = None
_kiosk_start_cmd = "/usr/local/bin/timing-talk-kiosk-start"
_kiosk_stop_cmd = "/usr/local/bin/timing-talk-kiosk-stop"
_kiosk_disabled_flag = None  # set in create_app


def create_app(network_manager, config=None, save_config_fn=None):
    global _network_manager, _config, _save_config_fn, _kiosk_disabled_flag
    _network_manager = network_manager
    _config = config or {}
    _save_config_fn = save_config_fn
    import os
    flag_dir = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "timing-talk",
    )
    _kiosk_disabled_flag = os.path.join(flag_dir, "kiosk-disabled")
    return app


@app.route("/")
def index():
    networks = _network_manager.scan_wifi() if _network_manager else []
    saved = _network_manager.get_saved_networks() if _network_manager else []
    device_ip = _network_manager.ip_address if _network_manager else "192.168.4.1"
    mode = _network_manager.mode if _network_manager else "unknown"
    connected_ssid = _network_manager.connected_ssid if _network_manager else ""
    track_name = _config.get("track_name", "") if _config else ""
    track_location = _config.get("track_location", "") if _config else ""
    event_name = _config.get("event_name", "") if _config else ""
    tcp_port = _config.get("tcp", {}).get("port", 4000) if _config else 4000
    kiosk_running = _is_kiosk_running()
    kiosk_disabled = _is_kiosk_disabled()
    remote_access = support_snapshot(_config or {})
    return render_template("setup.html",
                           networks=networks,
                           saved_networks=saved,
                           device_ip=device_ip,
                           mode=mode,
                           connected_ssid=connected_ssid,
                           track_name=track_name,
                           track_location=track_location,
                           event_name=event_name,
                           effective_event_name=_effective_event_name(event_name),
                           tcp_port=tcp_port,
                           kiosk_running=kiosk_running,
                           kiosk_disabled=kiosk_disabled,
                           remote_access=remote_access)


@app.route("/connect", methods=["POST"])
def connect_wifi():
    ssid = request.form.get("ssid", "").strip()
    password = request.form.get("password", "").strip()
    track_name = request.form.get("track_name", "").strip()
    track_location = request.form.get("track_location", "").strip()
    event_name = request.form.get("event_name", "").strip()

    if _config is not None:
        _config["track_name"] = track_name
        _config["track_location"] = track_location
        _config["event_name"] = event_name
        if _save_config_fn:
            _save_config_fn(_config)
        logger.info("Track settings updated: %s (%s) / %s", track_name, track_location, _effective_event_name(event_name))

    if not ssid:
        return _render_with(error="Please select a network",
                            track_name=track_name, track_location=track_location, event_name=event_name)

    if _network_manager.connect_wifi(ssid, password):
        new_ip = _network_manager.ip_address
        return _render_with(
            success=f"Connected to {ssid}! Device is now at {new_ip}:8080",
            track_name=track_name, track_location=track_location, event_name=event_name)
    else:
        return _render_with(error=f"Failed to connect to {ssid}. Check password.",
                            track_name=track_name, track_location=track_location, event_name=event_name)


@app.route("/forget", methods=["POST"])
def forget_wifi():
    ssid = request.form.get("ssid", "").strip()
    if ssid and _network_manager:
        _network_manager.forget_network(ssid)
    return _render_with(success=f"Forgot network: {ssid}")


@app.route("/settings", methods=["POST"])
def save_settings():
    track_name = request.form.get("track_name", "").strip()
    track_location = request.form.get("track_location", "").strip()
    event_name = request.form.get("event_name", "").strip()
    if _config is not None:
        _config["track_name"] = track_name
        _config["track_location"] = track_location
        _config["event_name"] = event_name
        if _save_config_fn:
            _save_config_fn(_config)
    return _render_with(success="Settings saved.",
                        track_name=track_name, track_location=track_location, event_name=event_name)


@app.route("/kiosk/stop", methods=["POST"])
def stop_kiosk():
    try:
        subprocess.run([_kiosk_stop_cmd], check=False)
        return _render_with(success="Switched to Pi Desktop mode. Kiosk will not start on next boot.")
    except Exception as exc:
        logger.exception("Failed to stop kiosk mode")
        return _render_with(error=f"Could not stop kiosk mode: {exc}")


@app.route("/kiosk/start", methods=["POST"])
def start_kiosk():
    try:
        subprocess.Popen(
            [_kiosk_start_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _render_with(success="Kiosk mode starting. It will also auto-start on next boot.")
    except Exception as exc:
        logger.exception("Failed to start kiosk mode")
        return _render_with(error=f"Could not start kiosk mode: {exc}")


@app.route("/scan")
def scan():
    networks = _network_manager.scan_wifi() if _network_manager else []
    return jsonify(networks)


@app.route("/status")
def status():
    return jsonify({
        "mode": _network_manager.mode if _network_manager else "unknown",
        "ip": _network_manager.ip_address if _network_manager else "",
        "connected_ssid": _network_manager.connected_ssid if _network_manager else "",
        "internet": _network_manager.has_internet() if _network_manager else False,
        "track_name": _config.get("track_name", "") if _config else "",
        "event_name": _config.get("event_name", "") if _config else "",
        "effective_event_name": _effective_event_name(_config.get("event_name", "") if _config else ""),
        "saved_networks": _network_manager.get_saved_networks() if _network_manager else [],
        "kiosk_running": _is_kiosk_running(),
        "kiosk_disabled": _is_kiosk_disabled(),
        "remote_access": support_snapshot(_config or {}),
    })


@app.route("/<path:path>")
def catch_all(path):
    return redirect("/")


def _render_with(error=None, success=None, track_name=None, track_location=None, event_name=None):
    networks = _network_manager.scan_wifi() if _network_manager else []
    saved = _network_manager.get_saved_networks() if _network_manager else []
    device_ip = _network_manager.ip_address if _network_manager else "192.168.4.1"
    mode = _network_manager.mode if _network_manager else "unknown"
    connected_ssid = _network_manager.connected_ssid if _network_manager else ""
    tn = track_name if track_name is not None else (_config.get("track_name", "") if _config else "")
    tl = track_location if track_location is not None else (_config.get("track_location", "") if _config else "")
    en = event_name if event_name is not None else (_config.get("event_name", "") if _config else "")
    tcp_port = _config.get("tcp", {}).get("port", 4000) if _config else 4000
    kiosk_running = _is_kiosk_running()
    kiosk_disabled = _is_kiosk_disabled()
    remote_access = support_snapshot(_config or {})
    return render_template("setup.html",
                           networks=networks,
                           saved_networks=saved,
                           device_ip=device_ip,
                           mode=mode,
                           connected_ssid=connected_ssid,
                           track_name=tn,
                           track_location=tl,
                           event_name=en,
                           effective_event_name=_effective_event_name(en),
                           tcp_port=tcp_port,
                           kiosk_running=kiosk_running,
                           kiosk_disabled=kiosk_disabled,
                           remote_access=remote_access,
                           error=error,
                           success=success)


def _effective_event_name(event_name: str) -> str:
    current_date = datetime.now().strftime("%Y-%m-%d")
    base_name = (event_name or "").strip()
    if base_name:
        return f"{base_name} - {current_date}"
    return current_date


def _is_kiosk_running():
    try:
        result = subprocess.run(
            ["pgrep", "-f", "timing-talk-kiosk-profile"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _is_kiosk_disabled():
    import os
    return _kiosk_disabled_flag is not None and os.path.isfile(_kiosk_disabled_flag)
