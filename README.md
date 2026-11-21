# AlienFX Lighting Studio

A native Linux app for editing Alienware laptop lighting per key, saving named profiles, and switching them from the system tray.

- Recolor keys, the power button, touchpad, rear ring, and light bars with live preview
- Save, rename, and delete profiles
- Switch profiles from the tray or the command line
- Restore the active profile at login
- Never writes to controller flash, so edits cannot wear it out

## Supported hardware

| Model | Keyboard | Lighting controller | Status |
|---|---|---|---|
| Alienware x17 R1 | Darfon `0d62:babc` | AW-ELC `187c:0550` | Verified |

Other models may work if they use the same controllers, but their key maps can differ. See [Other models](#other-models).

Check your hardware with:

```sh
lsusb | grep -Ei '0d62:babc|187c:0550'
```

Both devices should be listed.

## Install

Install the dependencies for your distribution:

```sh
# Arch / CachyOS
sudo pacman -S --needed base-devel git cmake libusb python-pyqt6

# Debian / Ubuntu
sudo apt install build-essential git cmake pkg-config libusb-1.0-0-dev libudev-dev python3-pyqt6

# Fedora
sudo dnf install gcc-c++ git cmake pkgconf-pkg-config libusb1-devel systemd-devel python3-pyqt6
```

A compiler with C++23 support is required. Development and testing happen on Arch-based systems with KDE Plasma on Wayland. The Debian and Fedora package lists are untested.

Then run:

```sh
git clone https://github.com/echobattalion/alienfx-studio.git
cd alienfx-studio
./install.sh
```

The installer:

1. Confirms the supported hardware is present.
2. Downloads a pinned commit of the [alienfx-linux fork](https://github.com/echobattalion/alienfx-linux/tree/alienfx-studio) and builds `alienfx_cli`.
3. Installs the app to `~/.local/share/alienfx-studio` and the `alienfx-studio` command to `~/.local/bin`.
4. Installs a udev rule so your user can access the lighting devices. This is the only step that uses `sudo`.
5. Enables a login service and tray autostart.

Run `./install.sh --help` for options. If the lights do not respond right after installing, log out and back in so the device permissions take effect.

## Usage

```sh
alienfx-studio                  # open the editor
alienfx-studio list             # list profiles (* marks the active one)
alienfx-studio apply            # apply the active profile
alienfx-studio apply "Gaming"   # apply a profile and make it active
```

In the editor, click a key to select it, Ctrl-click to add or remove keys, or drag across a group. Picking a color updates the laptop immediately. Press **Save** to keep the change.

The active profile is the one restored at login. It changes whenever you save or switch profiles.

Profiles are JSON files in `~/.config/alienfx/profiles/`.

### Update speed

The keyboard updates in under 100 ms. The AW-ELC is slower: about 300 ms for one zone and about 2 seconds for rear zone 19, which needs a separate firmware command. The first write after opening the app sends everything and takes about 4 seconds.

## Other models

Lights are addressed by index, and `alienfx_studio/kbmap.py` maps those indices to key names. To adapt another model:

1. Install with `./install.sh --force`.
2. Run the CLI's `probe` command to identify your lights: `~/.local/share/alienfx-studio/bin/alienfx_cli probe`.
3. Update `kbmap.py` and, if needed, the geometry in `alienfx_studio/layout.py`.

If your devices enumerate in a different order, set `ALIENFX_KEYBOARD_DEV` and `ALIENFX_ELC_DEV`. Contributions of verified maps are welcome.

## Troubleshooting

- **`Live: failed` in the status bar:** Check that the udev rule is installed, then log out and back in.
- **Wrong lights change:** Your device order or key map differs. See [Other models](#other-models).
- **No tray icon:** Your desktop may not support system tray icons. The editor still works as a normal window.
- **Login service:** Inspect it with `journalctl --user -u alienfx-studio.service`.

## Development

```sh
python3 -m unittest discover -s tests
PYTHONPATH=. python3 -m alienfx_studio
```

Tests use a fake CLI and never touch real lights.

## Uninstall

```sh
./uninstall.sh                # keeps your profiles
./uninstall.sh --purge        # also deletes profiles
./uninstall.sh --remove-udev  # also removes the udev rule
```

## Credits and license

Hardware access is provided by [alienfx-linux](https://github.com/tr1xem/alienfx-linux) by tr1xem, through [a GPL-3.0 fork](https://github.com/echobattalion/alienfx-linux/tree/alienfx-studio) that adds batch updates and pins its dependencies. This project is licensed under GPL-3.0. See [LICENSE](LICENSE).

This is an unofficial community project and is not affiliated with or endorsed by Dell or Alienware.
