#!/bin/bash
set -euo pipefail

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/timing-talk-kiosk-profile"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}"
LOG_FILE="$LOG_DIR/timing-talk-kiosk.log"
FLAG_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/timing-talk"
DISABLED_FLAG="$FLAG_DIR/kiosk-disabled"
PI_UID="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${PI_UID}}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export DISPLAY="${DISPLAY:-:0}"
export HOME="${HOME:-/home/pi}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"
export GTK_IM_MODULE="${GTK_IM_MODULE:-wayland}"
export QT_IM_MODULE="${QT_IM_MODULE:-wayland}"
export XMODIFIERS="${XMODIFIERS:-@im=wayland}"

mkdir -p "$PROFILE_DIR" "$LOG_DIR" "$FLAG_DIR"

# Clear the disabled flag — explicitly starting kiosk means the user wants kiosk mode
rm -f "$DISABLED_FLAG"

browser=""
for candidate in /usr/lib/chromium/chromium chromium-browser chromium google-chrome; do
  if command -v "$candidate" >/dev/null 2>&1; then
    browser="$candidate"
    break
  fi
done

if [ -z "$browser" ]; then
  echo "No supported browser found. Install Chromium first." >&2
  exit 1
fi

for _ in $(seq 1 45); do
  if [ -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ] && pgrep -u "$PI_UID" -x labwc >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if [ ! -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]; then
  echo "Wayland socket not ready at ${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" >>"$LOG_FILE"
  exit 1
fi

url="http://127.0.0.1/kiosk"
if command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if pgrep -af "$PROFILE_DIR" >/dev/null 2>&1; then
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    kill "$pid" >/dev/null 2>&1 || true
  done < <(ps -eo pid=,args= | grep "$PROFILE_DIR" | grep -E '/chromium|chromium-browser|google-chrome' | awk '{print $1}')
  sleep 1
fi

nohup "$browser" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --password-store=basic \
  --ozone-platform=wayland \
  --disable-gpu \
  --disable-gpu-compositing \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --check-for-update-interval=31536000 \
  --kiosk \
  "$url" >>"$LOG_FILE" 2>&1 &
