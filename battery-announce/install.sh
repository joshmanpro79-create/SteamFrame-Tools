#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 joshmanpro79-create
#
# battery-announce installer. User-level only: no sudo, no Developer Mode.
# Run from Desktop Mode > Konsole:  bash install.sh
#
# Safe to run again to upgrade: downloads are skipped when already present and
# your settings (~/.config/battery-announce/config.json) are never touched.
#
# What it puts where (all inside your home folder):
#   ~/.local/bin/battery-announce                 the program
#   ~/.local/share/battery-announce/piper/        Piper text-to-speech engine
#   ~/.local/share/battery-announce/voices/       the voice model
#   ~/.local/share/battery-announce/gui-python/   private Python for the windows,
#                                                 only if SteamOS's can't draw them
#   ~/.config/systemd/user/battery-announce.service   starts it at every login
#   ~/.local/share/applications/battery-*.desktop     launchers to add to Steam

# Stop on any error (-e), on unset variables (-u) and on failures inside pipes.
set -euo pipefail

# Folder this script is in (where the other package files are).
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
DATA="$HOME/.local/share/battery-announce"
UNIT_DIR="$HOME/.config/systemd/user"
APPS="$HOME/.local/share/applications"
# Voice and Piper download locations. Any Piper voice works; change both
# VOICE and VOICE_URL together to use a different one.
VOICE="en_US-lessac-medium"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
PIPER_REL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2"
# Private Python for the windows (see step 1). micromamba is a small,
# self-contained package manager that installs into a folder without sudo.
# The version and checksums are pinned so a changed download is rejected.
MAMBA_REL="https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0"
GUI_PY_DIR="$DATA/gui-python"
GUI_PY="$GUI_PY_DIR/bin/python3"
# Python 3.12 plus the Tk window toolkit built *with* font support ("xft").
# The other Tk build ("noxft") can only draw tiny bitmap text.
GUI_PY_SPECS=("python=3.12" "tk=8.6.*=xft*")

# Print a progress heading / a warning / a fatal error.
say()  { printf '\n==> %s\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Pick the downloads for this CPU (the Steam Frame is ARM64 / aarch64).
case "$(uname -m)" in
  aarch64|arm64)
    PIPER_TAR="piper_linux_aarch64.tar.gz"
    MAMBA_FILE="micromamba-linux-aarch64"
    MAMBA_SHA256="3bb56786e21b9b24024f599fe9185b4a59dc3f9f59937a42311ee583adbbb23c" ;;
  x86_64)
    PIPER_TAR="piper_linux_x86_64.tar.gz"
    MAMBA_FILE="micromamba-linux-64"
    MAMBA_SHA256="701f39c06f8e6a670a0e186c8479b9e5c0502a2f0cda6cef65d83bdd2875e8bd" ;;
  *) die "Unsupported CPU: $(uname -m)" ;;
esac

# 0. Requirements
#
# Everything here ships with SteamOS. Things that would need sudo to add are
# reported and stop the install; everything else is fixed in step 1.
say "Checking requirements"
missing=()
for cmd in python3 curl tar sha256sum systemctl; do
  command -v "$cmd" >/dev/null || missing+=("$cmd")
done
[ ${#missing[@]} -eq 0 ] || die "Missing programs: ${missing[*]}. These are part of SteamOS and can't be added without sudo."
# Python 3.8+ with every standard-library module the program imports.
python3 - <<'EOF' || die "SteamOS's Python is missing parts the program needs (see above)."
import sys
if sys.version_info < (3, 8):
    sys.exit(f"Python 3.8 or newer is needed; found {sys.version.split()[0]}")
missing = []
for m in ("glob", "hashlib", "json", "os", "shutil", "signal", "subprocess", "tempfile", "time"):
    try:
        __import__(m)
    except ImportError:
        missing.append(m)
if missing:
    sys.exit("Missing Python modules: " + ", ".join(missing))
print(f"    Python {sys.version.split()[0]}: all required modules present")
EOF
# Audio: any one of these players works. Without one the voice can't be heard.
player=""
for p in pw-play pw-cat paplay aplay; do
  if command -v "$p" >/dev/null; then player="$p"; break; fi
done
if [ -n "$player" ]; then echo "    Audio player: $player"; else warn "no audio player found (pw-play, paplay or aplay); announcements will be silent"; fi

# 1. Window toolkit for Battery Calibrate and Battery Alerts
#
# tk_ok PYTHON: succeeds if that Python can open a window AND draw large text.
# Exit codes from the check: 1 = tkinter missing or no window, 2 = only tiny
# bitmap fonts. Without a display (e.g. over SSH) only the import is checked.
tk_ok() {
  "$1" - <<'EOF'
import os, sys
try:
    import tkinter, tkinter.font
except Exception as e:
    print(f"    tkinter not available: {e}")
    sys.exit(1)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    sys.exit(0)
try:
    root = tkinter.Tk()
    root.withdraw()
    height = tkinter.font.Font(family="DejaVu Sans", size=30).metrics("linespace")
    root.destroy()
except Exception as e:
    print(f"    tkinter can't open a window: {e}")
    sys.exit(1)
if height < 30:
    print("    tkinter only has tiny bitmap fonts (built without Xft)")
    sys.exit(2)
EOF
}

# install_gui_python: download micromamba, verify it, create the private
# Python in $GUI_PY_DIR, then delete micromamba and its download cache.
install_gui_python() {
  local tmp
  tmp="$(mktemp -d)"
  curl -fL --progress-bar -o "$tmp/micromamba" "$MAMBA_REL/$MAMBA_FILE"
  echo "$MAMBA_SHA256  $tmp/micromamba" | sha256sum -c --quiet - \
    || { rm -rf "$tmp"; die "micromamba download failed its checksum check; not installing it."; }
  chmod +x "$tmp/micromamba"
  rm -rf "$GUI_PY_DIR"
  # The cache (MAMBA_ROOT_PREFIX) lives in the temp folder so it's removed after.
  MAMBA_ROOT_PREFIX="$tmp/root" "$tmp/micromamba" create -y -q -p "$GUI_PY_DIR" \
    -c conda-forge --override-channels "${GUI_PY_SPECS[@]}"
  rm -rf "$tmp"
}

say "Checking the window toolkit (for Battery Calibrate and Battery Alerts)"
mkdir -p "$DATA"
if tk_ok python3; then
  echo "    SteamOS's Python can draw the windows; nothing to add."
  rm -rf "$GUI_PY_DIR"          # not needed (left over from an earlier install?)
elif [ -x "$GUI_PY" ] && tk_ok "$GUI_PY"; then
  echo "    Private window Python already installed."
else
  say "Installing a private Python with the window toolkit (about 70 MB download)"
  install_gui_python
  if tk_ok "$GUI_PY"; then
    echo "    Window toolkit ready."
  else
    warn "the windows still can't be drawn. Battery Calibrate will use a simple dialog and"
    warn "Battery Alerts will point to the Konsole commands instead. Announcements are unaffected."
  fi
fi

mkdir -p "$BIN" "$DATA/voices" "$UNIT_DIR" "$APPS"

# 2. Piper and the voice
#
# piper_ok PATH: succeeds if the Piper program at PATH can actually turn a
# word into a non-empty WAV file with our voice (catches missing libraries).
piper_ok() {
  local p="$1" out
  out="$(mktemp --suffix=.wav)"
  echo "test" | (cd "$(dirname "$p")" && "$p" -m "$DATA/voices/$VOICE.onnx" -f "$out") >/dev/null 2>&1 \
    && [ -s "$out" ]; local rc=$?; rm -f "$out"; return $rc
}

say "Downloading voice ($VOICE, about 60 MB)"
# The voice is a model file plus its JSON settings. Each downloads to a .part
# file first, so an interrupted download is never mistaken for a finished one.
for f in "$VOICE.onnx" "$VOICE.onnx.json"; do
  if [ ! -s "$DATA/voices/$f" ]; then
    curl -fL --progress-bar -o "$DATA/voices/$f.part" "$VOICE_URL/$f"
    mv "$DATA/voices/$f.part" "$DATA/voices/$f"
  fi
done

# Reuse a working Piper; otherwise download the prebuilt release. If that
# build can't run on this system, fall back to Piper's Python package
# installed into a private virtual environment (still no sudo).
if [ -x "$DATA/piper/piper" ] && piper_ok "$DATA/piper/piper"; then
  say "Piper already installed"
else
  say "Downloading Piper ($PIPER_TAR)"
  tmp="$(mktemp -d)"
  curl -fL --progress-bar -o "$tmp/piper.tgz" "$PIPER_REL/$PIPER_TAR"
  rm -rf "$DATA/piper"
  tar -xzf "$tmp/piper.tgz" -C "$DATA"
  rm -rf "$tmp"
  if ! piper_ok "$DATA/piper/piper"; then
    say "Prebuilt Piper didn't run here; trying the Python package instead"
    python3 -m venv "$DATA/venv"
    "$DATA/venv/bin/pip" install --quiet --upgrade pip piper-tts
    piper_ok "$DATA/venv/bin/piper" || die "Piper could not be set up. See the guide's troubleshooting section."
  fi
fi

# 3. Script + service
say "Installing script and service"
install -m 755 "$HERE/battery-announce" "$BIN/battery-announce"
install -m 644 "$HERE/battery-announce.service" "$UNIT_DIR/battery-announce.service"

# 4. Launchers you can add to Steam (Add a Non-Steam Game)
#
# make_launcher FILE NAME COMMAND ICON writes a desktop entry that runs
# `battery-announce COMMAND`. Steam's "Add a Non-Steam Game" lists these.
# (The window commands switch to the private Python by themselves if it exists.)
make_launcher() {  # file, name, command, icon
  cat > "$APPS/$1.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Exec=/usr/bin/python3 $BIN/battery-announce $3
Icon=$4
Terminal=false
Categories=Utility;
EOF
}
make_launcher battery-readout   "Battery Readout"         now    battery
make_launcher battery-toggle    "Battery Announce On-Off" toggle audio-volume-muted
make_launcher battery-calibrate "Battery Calibrate"       calibrate-gui preferences-system
make_launcher battery-alerts    "Battery Alerts"          alerts-gui    preferences-desktop-notification
# Refresh the application menu if the tool exists (harmless if it doesn't).
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

# 5. Start it. Talk to the main login session's service manager directly,
#    because the nested Desktop Mode session may not reach it.
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
say "Enabling service"
systemctl --user daemon-reload                          # pick up the new/changed unit file
systemctl --user enable --now battery-announce.service  # start at every login, and now
systemctl --user restart battery-announce.service       # load the new version on upgrades

# 6. Show what it found and confirm the voice works.
say "Done. Current reading:"
python3 "$BIN/battery-announce" status || true
python3 "$BIN/battery-announce" say "Battery announcements ready." || true
cat <<EOF

Next:
  Calibrate:  python3 ~/.local/bin/battery-announce calibrate <the % Steam shows>
  Readout:    python3 ~/.local/bin/battery-announce now
  Mute:       python3 ~/.local/bin/battery-announce off   (on / toggle)
  Alerts:     python3 ~/.local/bin/battery-announce alerts      (alerts help for more)
  Steam:      Add a Non-Steam Game > "Battery Readout", "Battery Announce On-Off",
              "Battery Calibrate" and "Battery Alerts"
EOF
