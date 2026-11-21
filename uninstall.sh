#!/usr/bin/env bash
# Remove AlienFX Lighting Studio. Profiles are kept unless --purge is given.
set -euo pipefail

DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
PURGE=0 UDEV=0

for arg in "$@"; do
    case $arg in
        --purge) PURGE=1 ;;
        --remove-udev) UDEV=1 ;;
        -h|--help)
            echo "Usage: ./uninstall.sh [--purge] [--remove-udev]"
            echo "  --purge        Also delete saved profiles"
            echo "  --remove-udev  Also remove the udev rule (uses sudo)"
            exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

systemctl --user disable --now alienfx-studio.service 2>/dev/null || true
pkill -f 'python3 -m alienfx_studio' 2>/dev/null || true
rm -f "$CONFIG/systemd/user/alienfx-studio.service" \
      "$CONFIG/autostart/alienfx-studio-tray.desktop" \
      "$DATA/applications/alienfx-studio.desktop" \
      "$HOME/.local/bin/alienfx-studio"
rm -rf "$DATA/alienfx-studio"
systemctl --user daemon-reload 2>/dev/null || true

if ((PURGE)); then
    rm -rf "$CONFIG/alienfx/profiles" "$CONFIG/alienfx/state.json"
fi
if ((UDEV)); then
    sudo rm -f /etc/udev/rules.d/61-alienfx.rules
    sudo udevadm control --reload-rules
fi
echo "AlienFX Lighting Studio removed."
((PURGE)) || echo "Profiles were kept in $CONFIG/alienfx."
