#!/bin/bash
# Timing Talk - Stop kiosk and switch to Pi Desktop mode
# Sets a persistent flag so kiosk does NOT restart on next boot.
# To re-enable kiosk on boot, use "Enable Kiosk Mode" in the web UI
# or run: timing-talk-kiosk-start
set -euo pipefail

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/timing-talk-kiosk-profile"
FLAG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/timing-talk"
DISABLED_FLAG="$FLAG_DIR/kiosk-disabled"

# Set the flag so autostart won't re-launch on next boot
mkdir -p "$FLAG_DIR"
date > "$DISABLED_FLAG"

# Kill any running kiosk browser
pkill -f "$PROFILE_DIR" >/dev/null 2>&1 || true
