#!/bin/bash
# Timing Talk - Kiosk autostart on boot
# Launches kiosk mode UNLESS the user has explicitly chosen Pi Desktop mode.
# The flag file is created by "Exit Kiosk Mode" in the web UI and removed
# by "Enable Kiosk Mode" or on the next setup run.
set -euo pipefail

FLAG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/timing-talk"
DISABLED_FLAG="$FLAG_DIR/kiosk-disabled"

if [ -f "$DISABLED_FLAG" ]; then
    echo "$(date): Kiosk autostart skipped — Pi Desktop mode is active." \
        >>"${XDG_STATE_HOME:-$HOME/.local/state}/timing-talk-kiosk.log"
    echo "  To re-enable: delete $DISABLED_FLAG or use the Timing Talk web UI."
    exit 0
fi

# Give the desktop environment time to fully initialise
sleep 8

exec /usr/local/bin/timing-talk-kiosk-start
