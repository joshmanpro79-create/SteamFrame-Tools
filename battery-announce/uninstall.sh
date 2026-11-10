#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 joshmanpro79-create
#
# Removes everything install.sh added, including your calibration and the
# downloaded voice. Run from Desktop Mode > Konsole:  bash uninstall.sh
# (To upgrade, don't uninstall: just run the new install.sh.)

# Carry on past errors (a file may already be gone), but catch typos in variables.
set -u

# Reach the main login session's service manager, as install.sh does.
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
# Stop the service and remove it from start-up.
systemctl --user disable --now battery-announce.service 2>/dev/null
rm -f  "$HOME/.config/systemd/user/battery-announce.service"
systemctl --user daemon-reload 2>/dev/null
# The program and the four Steam launchers.
rm -f  "$HOME/.local/bin/battery-announce"
rm -f  "$HOME/.local/share/applications/battery-readout.desktop" \
       "$HOME/.local/share/applications/battery-toggle.desktop" \
       "$HOME/.local/share/applications/battery-calibrate.desktop" \
       "$HOME/.local/share/applications/battery-alerts.desktop"
# Piper and the voice, the private window Python, cached speech, and settings/calibration.
rm -rf "$HOME/.local/share/battery-announce" "$HOME/.cache/battery-announce" \
       "$HOME/.config/battery-announce"
echo "battery-announce removed. Remove the four Non-Steam shortcuts from your Steam library if you added them."
