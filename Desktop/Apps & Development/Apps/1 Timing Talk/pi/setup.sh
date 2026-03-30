#!/bin/bash
set -e

APP_DIR="/opt/timing-talk"
DATA_DIR="/var/lib/timing-talk"
SERVICE_NAME="timing-talk"
KIOSK_START_BIN="/usr/local/bin/timing-talk-kiosk-start"
KIOSK_STOP_BIN="/usr/local/bin/timing-talk-kiosk-stop"
KIOSK_AUTOSTART_BIN="/usr/local/bin/timing-talk-kiosk-autostart"

echo "========================================="
echo "  Timing Talk - Pi Setup"
echo "========================================="

# Must run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash setup.sh"
  exit 1
fi

echo ""
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv \
  ca-certificates curl \
  i2c-tools libopenjp2-7 libtiff6 \
  network-manager

echo ""
echo "[2/8] Enabling I2C interface..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
  echo "dtparam=i2c_arm=on" >> /boot/firmware/config.txt
  echo "  I2C enabled (reboot required for first use)"
fi

# Ensure i2c module loads
if ! grep -q "^i2c-dev" /etc/modules; then
  echo "i2c-dev" >> /etc/modules
fi
modprobe i2c-dev 2>/dev/null || true

echo ""
echo "[3/8] Setting up application directory..."
mkdir -p "$APP_DIR"
mkdir -p "$DATA_DIR/raw"

# Copy application files
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR"/*.py "$APP_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$APP_DIR/"
cp -r "$SCRIPT_DIR/captive_portal" "$APP_DIR/"
cp "$SCRIPT_DIR/kiosk-start.sh" "$APP_DIR/"
cp "$SCRIPT_DIR/kiosk-stop.sh" "$APP_DIR/"
cp "$SCRIPT_DIR/kiosk-autostart.sh" "$APP_DIR/"
cp "$SCRIPT_DIR/labwc-autostart" "$APP_DIR/"

echo ""
echo "  Bootstrapping device config..."
python3 - <<PY
import sys
sys.path.insert(0, "$APP_DIR")
from config import load_config, save_config

cfg = load_config()
save_config(cfg)
print(cfg["device_id"])
PY

echo ""
echo "[4/8] Creating Python virtual environment and installing dependencies..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo ""
echo "[5/8] Installing systemd service..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << 'UNIT'
[Unit]
Description=Timing Talk Serial Data Bridge
After=network.target NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStart=/opt/timing-talk/venv/bin/python /opt/timing-talk/main.py
WorkingDirectory=/opt/timing-talk
User=root
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Give access to serial ports, I2C, and GPIO
SupplementaryGroups=dialout i2c gpio

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo ""
echo "[6/8] Installing kiosk helpers..."
install -m 755 "$APP_DIR/kiosk-start.sh" "$KIOSK_START_BIN"
install -m 755 "$APP_DIR/kiosk-stop.sh" "$KIOSK_STOP_BIN"
install -m 755 "$APP_DIR/kiosk-autostart.sh" "$KIOSK_AUTOSTART_BIN"

DEFAULT_USER="$(getent passwd 1000 | cut -d: -f1 || true)"
if [ -n "$DEFAULT_USER" ] && [ -d "/home/$DEFAULT_USER" ]; then
  DESKTOP_DIR="/home/$DEFAULT_USER/Desktop"
  LABWC_DIR="/home/$DEFAULT_USER/.config/labwc"
  AUTOSTART_DIR="/home/$DEFAULT_USER/.config/autostart"
  mkdir -p "$DESKTOP_DIR"
  mkdir -p "$LABWC_DIR"
  mkdir -p "$AUTOSTART_DIR"
  install -m 755 "$SCRIPT_DIR/Timing Talk Kiosk.desktop" "$DESKTOP_DIR/Timing Talk Kiosk.desktop"
  install -m 644 "$SCRIPT_DIR/Timing Talk Kiosk Autostart.desktop" "$AUTOSTART_DIR/Timing Talk Kiosk Autostart.desktop"
  install -m 755 "$SCRIPT_DIR/labwc-autostart" "$LABWC_DIR/autostart"
  chown "$DEFAULT_USER:$DEFAULT_USER" "$DESKTOP_DIR/Timing Talk Kiosk.desktop"
  chown "$DEFAULT_USER:$DEFAULT_USER" "$AUTOSTART_DIR/Timing Talk Kiosk Autostart.desktop"
  chown "$DEFAULT_USER:$DEFAULT_USER" "$LABWC_DIR/autostart"
fi

echo ""
echo "[7/8] Configuring NetworkManager..."
# Ensure NetworkManager manages WiFi
if [ -f /etc/NetworkManager/NetworkManager.conf ]; then
  if ! grep -q "managed=true" /etc/NetworkManager/NetworkManager.conf; then
    sed -i 's/managed=false/managed=true/' /etc/NetworkManager/NetworkManager.conf 2>/dev/null || true
  fi
fi
systemctl restart NetworkManager 2>/dev/null || true

echo ""
echo "[8/8] Installing secure remote access (Tailscale)..."
if ! command -v tailscale >/dev/null 2>&1; then
  if curl -fsSL https://tailscale.com/install.sh -o /tmp/timing-talk-tailscale-install.sh && \
     sh /tmp/timing-talk-tailscale-install.sh; then
    rm -f /tmp/timing-talk-tailscale-install.sh
  else
    echo "  Tailscale install skipped (network or package error)."
    rm -f /tmp/timing-talk-tailscale-install.sh
  fi
fi
if command -v tailscale >/dev/null 2>&1; then
  systemctl enable tailscaled >/dev/null 2>&1 || true
  systemctl restart tailscaled >/dev/null 2>&1 || true

  TAILSCALE_HOSTNAME="$(python3 - <<PY
import json
import re
from pathlib import Path

config_path = Path("$DATA_DIR/config.json")
device_id = "device"
if config_path.exists():
    data = json.loads(config_path.read_text())
    device_id = str(data.get("device_id", "device")).strip().lower()
slug = re.sub(r"[^a-z0-9-]", "", device_id.replace("_", "-"))[:12] or "device"
print(f"timingtalk-{slug}")
PY
)"

  if [ -n "${TAILSCALE_AUTH_KEY:-}" ]; then
    if tailscale up --auth-key "${TAILSCALE_AUTH_KEY}" --accept-dns=true --hostname "${TAILSCALE_HOSTNAME}"; then
      echo "  Joined tailnet as ${TAILSCALE_HOSTNAME}"
    else
      echo "  Tailscale installed, but auto-login failed."
      echo "    sudo tailscale up --accept-dns=true --hostname ${TAILSCALE_HOSTNAME}"
    fi
  else
    echo "  Tailscale installed. Finish one-time login with:"
    echo "    sudo tailscale up --accept-dns=true --hostname ${TAILSCALE_HOSTNAME}"
  fi
else
  TAILSCALE_HOSTNAME="timingtalk-device"
fi

echo ""
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "  Next steps:"
echo "  1. Place your Firebase credentials JSON at:"
echo "     $DATA_DIR/firebase-credentials.json"
echo ""
echo "  2. Edit the config (optional):"
echo "     $DATA_DIR/config.json"
echo ""
echo "  3. Start the service:"
echo "     sudo systemctl start $SERVICE_NAME"
echo ""
echo "  4. View logs:"
echo "     sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "  5. Remote support (recommended for Cursor):"
echo "     sudo tailscale up --accept-dns=true --hostname ${TAILSCALE_HOSTNAME}"
echo ""
echo "  The service will auto-start on boot."
echo "========================================="
