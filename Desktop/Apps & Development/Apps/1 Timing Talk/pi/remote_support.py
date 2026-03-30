import json
import logging
import os
import re
import shutil
import subprocess
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger("timing-talk.remote-support")

SERVICE_NAME = "timing-talk"
KIOSK_START_CMD = "/usr/local/bin/timing-talk-kiosk-start"
KIOSK_STOP_CMD = "/usr/local/bin/timing-talk-kiosk-stop"
MAX_LOG_CHARS = 12000

ALLOWED_ACTIONS = {
    "diagnostics",
    "fetch_logs",
    "start_kiosk",
    "stop_kiosk",
    "restart_kiosk",
    "restart_app",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def desired_tailscale_hostname(config: dict) -> str:
    device_id = str(config.get("device_id", "device")).strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "", device_id.replace("_", "-"))[:12] or "device"
    return f"timingtalk-{slug}"


def tailscale_status(config: dict) -> dict:
    desired_host = desired_tailscale_hostname(config)
    status = {
        "installed": shutil.which("tailscale") is not None,
        "connected": False,
        "backendState": "not_installed",
        "hostname": desired_host,
        "dnsName": "",
        "ip": "",
        "sshHost": desired_host,
        "sshUser": "pi",
    }

    if not status["installed"]:
        return status

    status["backendState"] = "unknown"

    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            status["backendState"] = "error"
            return status

        data = json.loads(result.stdout or "{}")
        self_info = data.get("Self", {}) or {}
        dns_name = str(self_info.get("DNSName") or "").rstrip(".")
        ips = self_info.get("TailscaleIPs") or []
        backend_state = str(data.get("BackendState") or "unknown")

        status["backendState"] = backend_state
        status["dnsName"] = dns_name
        status["ip"] = ips[0] if ips else ""
        status["connected"] = backend_state.lower() == "running" and bool(dns_name or status["ip"])
        if dns_name:
            status["sshHost"] = dns_name
        return status
    except Exception as exc:
        logger.debug("Could not read Tailscale status: %s", exc)
        status["backendState"] = "error"
        return status


def support_capabilities() -> list[str]:
    return [
        "diagnostics",
        "fetch_logs",
        "start_kiosk",
        "stop_kiosk",
        "restart_kiosk",
        "restart_app",
    ]


def support_snapshot(config: dict) -> dict:
    return {
        "tailscale": tailscale_status(config),
        "capabilities": support_capabilities(),
        "updatedAt": iso_now(),
    }


def execute_support_action(action: str, payload: dict | None, config: dict) -> dict:
    normalized = str(action or "").strip()
    if normalized not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported support action: {normalized}")

    if normalized == "diagnostics":
        return {
            "ok": True,
            "action": normalized,
            "message": "Diagnostics collected.",
            "snapshot": support_snapshot(config),
        }

    if normalized == "fetch_logs":
        lines = int((payload or {}).get("lines") or 120)
        lines = max(20, min(lines, 300))
        return {
            "ok": True,
            "action": normalized,
            "message": f"Fetched last {lines} log lines.",
            "logs": _read_recent_logs(lines),
        }

    if normalized == "start_kiosk":
        _run_local([KIOSK_START_CMD], timeout=20)
        return {
            "ok": True,
            "action": normalized,
            "message": "Kiosk start requested.",
        }

    if normalized == "stop_kiosk":
        _run_local([KIOSK_STOP_CMD], timeout=20)
        return {
            "ok": True,
            "action": normalized,
            "message": "Kiosk stop requested.",
        }

    if normalized == "restart_kiosk":
        _run_local([KIOSK_STOP_CMD], timeout=20)
        _run_local([KIOSK_START_CMD], timeout=20)
        return {
            "ok": True,
            "action": normalized,
            "message": "Kiosk restart requested.",
        }

    _schedule_service_restart()
    return {
        "ok": True,
        "action": normalized,
        "message": "Timing Talk service restart scheduled.",
    }


def _run_local(cmd: list[str], timeout: int = 15):
    subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _read_recent_logs(lines: int) -> str:
    log_path = "/var/log/timing-talk.log"
    if not os.path.exists(log_path):
        return "Timing Talk log file not found."

    recent = deque(maxlen=lines)
    with open(log_path, "r", errors="replace") as handle:
        for line in handle:
            recent.append(line.rstrip())

    text = "\n".join(recent).strip()
    if len(text) > MAX_LOG_CHARS:
        text = text[-MAX_LOG_CHARS:]
    return text or "No log output yet."


def _schedule_service_restart():
    subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 2 && systemctl restart {SERVICE_NAME} >/dev/null 2>&1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
