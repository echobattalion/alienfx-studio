"""Command line: alienfx-studio [gui [--tray] | apply [name] | list]."""
import argparse
import sys

from . import device
from .profiles import ProfileError, Store


def main():
    parser = argparse.ArgumentParser(prog="alienfx-studio")
    sub = parser.add_subparsers(dest="cmd")
    gui = sub.add_parser("gui", help="open the editor (default)")
    gui.add_argument("--tray", action="store_true", help="start hidden in the tray")
    apply_cmd = sub.add_parser("apply", help="apply a profile (default: the active one)")
    apply_cmd.add_argument("name", nargs="?")
    sub.add_parser("list", help="list profiles")
    args = parser.parse_args()

    if args.cmd in (None, "gui"):
        from .app import run     # Qt is not needed for login or CLI use
        return run(start_hidden=getattr(args, "tray", False))

    try:
        store = Store()
        if args.cmd == "list":
            active = store.active_name()
            for _, name in store.list():
                print(f"{'*' if name == active else ' '} {name}")
            return 0
        profile = store.load(args.name or store.active_name())
        device.send(device.build_frame(profile))
        store.set_active(profile.name)
        print(f"Applied {profile.name}")
        return 0
    except (ProfileError, device.DeviceError) as e:
        print(f"alienfx-studio: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
