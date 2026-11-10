#!/usr/bin/env bash
# Copyright (c) 2026 joshmanpro79-create. Released under the MIT License.
# Frame Automations installer for Steam Frame.
# User-level only: no sudo, no Developer Mode, nothing outside your home folder.
set -u

APP=frame-automations
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
fail() { printf '  ✗ %s\n' "$*"; exit 1; }

fetch() {   # fetch URL -> stdout
  if command -v curl >/dev/null; then curl -fsSL "$1"; else wget -qO- "$1"; fi
}
download() {   # download URL FILE
  if command -v curl >/dev/null; then curl -fL --progress-bar -o "$2" "$1"; else wget -O "$2" "$1"; fi
}
has_tk() { "$1" -c 'import tkinter; tkinter.Tcl()' >/dev/null 2>&1; }

[ "$(id -u)" -ne 0 ] || fail "Run this as your normal user, not with sudo."

step "Checking what's needed"
PY="$(command -v python3)" || fail "python3 wasn't found."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || fail "Python 3.9 or newer is needed."
"$PY" -c 'import zoneinfo' 2>/dev/null || fail "Python's zoneinfo module is missing."
ok "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"
command -v systemctl >/dev/null || fail "systemctl wasn't found."
ok "systemd user services available"
PLAYER=""
for p in pw-play paplay aplay; do
  if command -v "$p" >/dev/null; then PLAYER="$p"; break; fi
done
if [ -n "$PLAYER" ]; then ok "Audio player: $PLAYER"; else warn "No audio player found (pw-play, paplay or aplay); sounds won't play."; fi
if command -v curl >/dev/null || command -v wget >/dev/null; then ok "Downloader available"; else fail "Neither curl nor wget was found."; fi
[ -f "$HERE/frame-automations.py" ] || fail "frame-automations.py is missing next to install.sh."

step "Installing the program"
mkdir -p "$BIN" "$DATA"
install -m 755 "$HERE/frame-automations.py" "$BIN/$APP"
ok "$BIN/$APP"

step "Checking the window toolkit"
WINPY=""
if has_tk "$PY"; then
  WINPY="$PY"
  ok "SteamOS's Python can draw the Frame Automations window."
else
  for cand in "$DATA/python/bin/python3" "$HOME"/.local/share/*/python/bin/python3; do
    if [ -x "$cand" ] && has_tk "$cand"; then
      WINPY="$cand"; ok "Using an existing window Python: $cand"; break
    fi
  done
fi
if [ -z "$WINPY" ]; then
  echo "  SteamOS's Python can't draw windows (no tkinter). Installing a private copy"
  echo "  (about 70 MB download, 190 MB on disk, only in your home folder)…"
  case "$(uname -m)" in
    aarch64|arm64) ARCH=aarch64 ;;
    x86_64|amd64)  ARCH=x86_64 ;;
    *) ARCH="" ;;
  esac
  URL=""
  if [ -n "$ARCH" ]; then
    URL="$(fetch https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest 2>/dev/null | "$PY" -c '
import json, re, sys
arch = sys.argv[1]
pat = re.compile(r"cpython-3\.(\d+)\.(\d+)\+\d+-" + arch + r"-unknown-linux-gnu-install_only\.tar\.gz$")
best = None
for a in json.load(sys.stdin).get("assets", []):
    m = pat.search(a.get("name", ""))
    if m and int(m.group(1)) <= 12:
        key = (int(m.group(1)), int(m.group(2)))
        if best is None or key > best[0]:
            best = (key, a["browser_download_url"])
print(best[1] if best else "")
' "$ARCH" 2>/dev/null)"
    # If GitHub's release lookup is unavailable, use a build known to work.
    [ -n "$URL" ] || URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16+20261026-$ARCH-unknown-linux-gnu-install_only.tar.gz"
  fi
  if [ -n "$URL" ]; then
    TMP="$(mktemp -d)"
    if download "$URL" "$TMP/python.tar.gz" && rm -rf "$DATA/python" && tar -xzf "$TMP/python.tar.gz" -C "$DATA"; then
      if has_tk "$DATA/python/bin/python3"; then
        WINPY="$DATA/python/bin/python3"; ok "Private window Python installed."
      else
        warn "The private Python was installed but still can't draw windows."
      fi
    else
      warn "Downloading the private Python failed."
    fi
    rm -rf "$TMP"
  else
    warn "Couldn't find a private Python download for this processor."
  fi
fi
if [ -n "$WINPY" ]; then
  echo "$WINPY" > "$DATA/window-python"
else
  rm -f "$DATA/window-python"
  warn "The window can't be drawn; the Konsole commands still work (see README)."
fi

step "MP3/M4A support"
if command -v ffmpeg >/dev/null || [ -x "$DATA/bin/ffmpeg" ]; then
  ok "Already available."
else
  ans=""
  read -r -p "  Install MP3/M4A support now? (about 40 MB download) [Y/n] " ans || true
  case "$ans" in
    [nN]*) ok "Skipped. Choosing an MP3 or M4A file in the window offers to install it later." ;;
    *) "$PY" "$BIN/$APP" install-decoder || warn "Not installed; you can retry from the window later." ;;
  esac
fi

step "Speech voice"
VOICE="$("$PY" "$BIN/$APP" voice)"
if [ "$VOICE" != "none" ]; then
  ok "Speak actions use: $VOICE"
else
  warn "No voice found. Speak actions need Battery Announce's Piper voice (or espeak-ng)."
fi

step "Background service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$APP.service" <<EOF
[Unit]
Description=Frame Automations (Boot, Wake and Clock automations)

[Service]
Type=simple
ExecStart=$PY $BIN/$APP run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
if systemctl --user daemon-reload && systemctl --user enable "$APP" >/dev/null 2>&1 \
   && systemctl --user restart "$APP"; then
  sleep 1
  if systemctl --user is-active --quiet "$APP"; then ok "Service running."
  else warn "Service didn't start; check: journalctl --user -u $APP -n 50"; fi
else
  warn "Couldn't reach the user service manager. Run the two export lines in the README, then: systemctl --user enable --now $APP"
fi

step "Launchers"
mkdir -p "$APPS"
launcher() {   # slug name comment args icon
  cat > "$APPS/$APP-$1.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Comment=$3
Exec="$PY" "$BIN/$APP" $4
Icon=$5
Terminal=false
Categories=Utility;
EOF
  ok "$2"
}
launcher window  "Frame Automations"            "Edit your Boot, Wake and Clock automations" "gui"    "preferences-system-time"
launcher onoff   "Frame Automations On-Off"     "Turn all automations on or off"             "toggle" "system-run"
launcher stop    "Frame Automations Stop Sound" "Stop any sound an automation is playing"    "stop"   "media-playback-stop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1

step "Done"
echo "  Add the three launchers to Steam: Steam → Games → Add a Non-Steam Game to My Library,"
echo "  tick Frame Automations, Frame Automations On-Off and Frame Automations Stop Sound."
"$PY" "$BIN/$APP" say "Frame automations ready." >/dev/null 2>&1 || true
