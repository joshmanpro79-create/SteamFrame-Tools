#!/usr/bin/env bash
# Copyright (c) 2026 joshmanpro79-create. Released under the MIT License.
# Removes Frame Automations. Your own audio files and scripts are never touched.
set -u
APP=frame-automations
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$APP.service"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

systemctl --user disable --now "$APP" >/dev/null 2>&1
rm -f "$UNIT"
systemctl --user daemon-reload >/dev/null 2>&1
rm -f "$HOME/.local/bin/$APP"
rm -f "$APPS/$APP-window.desktop" "$APPS/$APP-onoff.desktop" "$APPS/$APP-stop.desktop"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/$APP" "${XDG_CACHE_HOME:-$HOME/.cache}/$APP"
echo "Removed the program, service, launchers, private Python and MP3/M4A support."

ans=""
read -r -p "Also delete your automations and logs? [y/N] " ans || true
case "$ans" in
  [yY]*) rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/$APP" "${XDG_STATE_HOME:-$HOME/.local/state}/$APP"
         echo "Deleted your automations and logs." ;;
  *)     echo "Kept your automations (~/.config/$APP) and logs (~/.local/state/$APP/logs)." ;;
esac
echo "Remove the three Non-Steam shortcuts from your Steam library too."
