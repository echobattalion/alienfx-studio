#!/usr/bin/env bash
# Install AlienFX Lighting Studio for the current user.
set -euo pipefail

BACKEND=https://github.com/echobattalion/alienfx-linux.git
PINNED=41669e646e465a3df5f6899da332d0a51e2a4928  # tag studio-v1

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/alienfx-studio
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
APPS=${XDG_DATA_HOME:-$HOME/.local/share}/applications
BIN=$HOME/.local/bin

FORCE=0 REBUILD=0 UDEV=1 SERVICES=1

usage() {
    cat <<EOF
Usage: ./install.sh [options]

  --force        Install even if the supported lighting hardware is not found
  --rebuild      Rebuild alienfx_cli even if it is already installed
  --no-udev      Skip the udev rule (the only step that uses sudo)
  --no-services  Do not enable the login service or tray autostart
  -h, --help     Show this help
EOF
}

while (($#)); do
    case $1 in
        --force) FORCE=1 ;;
        --rebuild) REBUILD=1 ;;
        --no-udev) UDEV=0 ;;
        --no-services) SERVICES=0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

say() { printf '\n==> %s\n' "$1"; }
die() { printf 'error: %s\n' "$1" >&2; exit 1; }

has_usb() {
    local d
    for d in /sys/bus/usb/devices/*; do
        [[ -r $d/idVendor && $(<"$d/idVendor") == "$1" && $(<"$d/idProduct") == "$2" ]] && return 0
    done
    return 1
}

deps_hint() {
    if command -v pacman >/dev/null; then
        echo "sudo pacman -S --needed base-devel git cmake libusb python-pyqt6"
    elif command -v apt-get >/dev/null; then
        echo "sudo apt install build-essential git cmake pkg-config libusb-1.0-0-dev libudev-dev python3-pyqt6"
    elif command -v dnf >/dev/null; then
        echo "sudo dnf install gcc-c++ git cmake pkgconf-pkg-config libusb1-devel systemd-devel python3-pyqt6"
    else
        echo "Install: a C++23 compiler, git, cmake, make, pkg-config, libusb, libudev, PyQt6"
    fi
}

say "Checking hardware"
if has_usb 0d62 babc && has_usb 187c 0550; then
    echo "Found Darfon keyboard (0d62:babc) and AW-ELC (187c:0550)."
elif ((FORCE)); then
    echo "Supported hardware not found. Continuing because of --force."
else
    die "Darfon keyboard 0d62:babc and AW-ELC 187c:0550 were not both found.
The key map is only verified on the Alienware x17 R1. See 'Other models' in the
README, or rerun with --force."
fi

say "Checking dependencies"
missing=()
for tool in git cmake c++ pkg-config python3; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
python3 -c 'import PyQt6.QtWidgets, PyQt6.QtNetwork' 2>/dev/null || missing+=(PyQt6)
if ((${#missing[@]})); then
    die "Missing: ${missing[*]}
Install them with:
  $(deps_hint)"
fi

mkdir -p "$DATA/bin" "$BIN" "$APPS"

if ((REBUILD)) || [[ ! -x $DATA/bin/alienfx_cli ]]; then
    say "Building alienfx_cli (first build downloads its libraries)"
    rm -rf "$DATA/src"
    git clone --quiet "$BACKEND" "$DATA/src"
    git -C "$DATA/src" checkout --quiet "$PINNED"
    cmake -S "$DATA/src" -B "$DATA/src/build" \
        -DALIENFX_BUILD_CLI=ON -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    cmake --build "$DATA/src/build" --target alienfx_cli --parallel "$(nproc)"
    install -m 755 "$DATA/src/build/alienfx_cli" "$DATA/bin/alienfx_cli"
else
    say "Keeping existing alienfx_cli (use --rebuild to replace it)"
fi

say "Installing the app"
rm -rf "$DATA/app"
mkdir -p "$DATA/app"
cp -r "$REPO/alienfx_studio" "$DATA/app/"
find "$DATA/app" -name __pycache__ -type d -prune -exec rm -rf {} +

if [[ -e $BIN/alienfx-studio ]] && ! grep -q 'alienfx-studio/app' "$BIN/alienfx-studio"; then
    cp "$BIN/alienfx-studio" "$BIN/alienfx-studio.bak"
    echo "Backed up the existing launcher to $BIN/alienfx-studio.bak"
fi
cat > "$BIN/alienfx-studio" <<EOF
#!/bin/sh
PYTHONPATH="$DATA/app" exec python3 -m alienfx_studio "\$@"
EOF
chmod 755 "$BIN/alienfx-studio"

cat > "$APPS/alienfx-studio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AlienFX Lighting Studio
Comment=Edit keyboard and chassis lighting profiles
Exec=$BIN/alienfx-studio
Icon=input-keyboard
Categories=Settings;HardwareSettings;
EOF

if ((UDEV)); then
    say "Installing udev rule (needs sudo)"
    sudo install -m 644 "$REPO/packaging/61-alienfx.rules" /etc/udev/rules.d/61-alienfx.rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=hidraw
    sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=0d62
    sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=187c
fi

if ((SERVICES)); then
    say "Enabling login service and tray"
    mkdir -p "$CONFIG/systemd/user" "$CONFIG/autostart"
    install -m 644 "$REPO/packaging/alienfx-studio.service" "$CONFIG/systemd/user/alienfx-studio.service"
    install -m 644 "$REPO/packaging/alienfx-studio-watch.service" "$CONFIG/systemd/user/alienfx-studio-watch.service"
    cat > "$CONFIG/autostart/alienfx-studio-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AlienFX Lighting Studio Tray
Exec=$BIN/alienfx-studio gui --tray
Icon=input-keyboard
X-KDE-autostart-after=panel
EOF
    systemctl --user daemon-reload
    systemctl --user enable alienfx-studio.service alienfx-studio-watch.service
    if [[ -e $CONFIG/systemd/user/alienfx-scheme.service ]]; then
        echo "Note: an older alienfx-scheme.service exists. Disable it to avoid two login writers:"
        echo "  systemctl --user disable alienfx-scheme.service"
    fi
    systemctl --user restart alienfx-studio.service || \
        echo "Could not apply lighting yet. Log out and back in, then run: alienfx-studio apply"
    systemctl --user restart alienfx-studio-watch.service || \
        echo "Could not start the game watcher yet. It will start at next login."
fi

say "Done"
echo "Open the editor with: alienfx-studio"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Add $BIN to your PATH to use that command." ;;
esac
