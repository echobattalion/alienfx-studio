"""Command line: alienfx-studio [gui [--tray] | apply [name] | list | watch | games | ...]."""
import argparse
import sys

from . import device
from .profiles import ProfileError, Store


def _find_game(games, query):
    if query.isdigit():
        matches = [g for g in games if g.app_id == int(query)]
    else:
        wanted = query.lower()
        matches = [g for g in games if g.name.lower() == wanted] \
            or [g for g in games if wanted in g.name.lower()]
    if len(matches) != 1:
        problem = "matches several games" if matches else "matches no installed game"
        raise ProfileError(f"{query!r} {problem}")
    return matches[0]


def _find_type(table, name):
    found = table.type_named(name)
    if not found:
        raise ProfileError(f"no such game type: {name}")
    return found


def _print_games(store, table, games):
    width = max((len(g.name) for g in games), default=4)
    for g in games:
        game_type = table.type_of(g.key, g.tags)
        found = table.profile_for(g.key, g.tags, store.exists)
        profile = f"{found[0]} ({found[1]})" if found else "-"
        print(f"{g.app_id:>8}  {g.name:{width}}  {game_type.name if game_type else '-':11} {profile}")


def _rules_command(args, store):
    from . import rules
    from .steam import Library

    table = rules.load(store.root)
    cmd = args.cmd
    if cmd == "games":
        _print_games(store, table, Library().games())
        return 0
    if cmd == "types":
        for t in table.types:
            print(f"{t.name:12} {t.profile or '-':16} {', '.join(t.tags)}")
        return 0
    if cmd == "link":
        game = _find_game(Library().games(), args.game)
        profile = store.load(args.profile).name
        table.games.setdefault(game.key, rules.GameRule()).profile = profile
        message = f"{game.name} now uses {profile}"
    elif cmd == "unlink":
        game = _find_game(Library().games(), args.game)
        if game.key in table.games:
            table.games[game.key].profile = None
        message = f"{game.name} is no longer linked"
    elif cmd == "type-profile":
        game_type = _find_type(table, args.type)
        none = args.profile.lower() == "none"
        game_type.profile = None if none else store.load(args.profile).name
        message = f"{game_type.name} uses {game_type.profile or 'no profile'}"
    elif cmd == "set-type":
        game = _find_game(Library().games(), args.game)
        auto = args.type.lower() == "auto"
        chosen = None if auto else _find_type(table, args.type).name
        table.games.setdefault(game.key, rules.GameRule()).type = chosen
        message = f"{game.name} type: {chosen or 'automatic'}"
    else:    # type-order
        ordered = [_find_type(table, name) for name in args.types]
        if len({t.name for t in ordered}) != len(table.types) or len(ordered) != len(table.types):
            raise ProfileError("list every game type exactly once")
        table.types = ordered
        message = "Type order: " + ", ".join(t.name for t in ordered)
    rules.save(store.root, table)
    print(message)
    return 0


def main():
    parser = argparse.ArgumentParser(prog="alienfx-studio")
    sub = parser.add_subparsers(dest="cmd")
    gui = sub.add_parser("gui", help="open the editor (default)")
    gui.add_argument("--tray", action="store_true", help="start hidden in the tray")
    apply_cmd = sub.add_parser("apply", help="apply a profile (default: the default profile)")
    apply_cmd.add_argument("name", nargs="?")
    sub.add_parser("list", help="list profiles")
    default_cmd = sub.add_parser("default", help="set the default profile")
    default_cmd.add_argument("name")
    sub.add_parser("watch", help="switch profiles while linked games run")
    sub.add_parser("games", help="list installed Steam games, types, and profiles")
    sub.add_parser("types", help="list game types in match order")
    link = sub.add_parser("link", help="give a game its own profile")
    link.add_argument("game", help="Steam app ID or part of the name")
    link.add_argument("profile")
    unlink = sub.add_parser("unlink", help="remove a game's own profile")
    unlink.add_argument("game")
    type_profile = sub.add_parser("type-profile", help="set a game type's profile")
    type_profile.add_argument("type")
    type_profile.add_argument("profile", help="profile name, or none")
    set_type = sub.add_parser("set-type", help="override a game's type")
    set_type.add_argument("game")
    set_type.add_argument("type", help="type name, or auto")
    order = sub.add_parser("type-order", help="set the order types are matched in")
    order.add_argument("types", nargs="+")
    args = parser.parse_args()

    if args.cmd in (None, "gui"):
        from .app import run     # Qt is not needed for login or CLI use
        return run(start_hidden=getattr(args, "tray", False))
    if args.cmd == "watch":
        from .watcher import run as watch
        return watch()

    try:
        store = Store()
        if args.cmd == "list":
            active, current = store.active_name(), store.current_name()
            for _, name in store.list():
                mark = "*" if name == active else ">" if name == current else " "
                print(f"{mark} {name}")
            return 0
        if args.cmd == "default":
            store.set_default(args.name)
            print(f"Default profile: {store.active_name()}")
            return 0
        if args.cmd != "apply":
            return _rules_command(args, store)
        profile = store.load(args.name or store.active_name())
        device.send(device.build_frame(profile))
        if args.name:
            store.choose(profile.name)      # never changes the default profile
        else:
            store.set_active(profile.name)  # the login restore ends any temporary profile
        print(f"Applied {profile.name}")
        return 0
    except (ProfileError, device.DeviceError, OSError) as e:
        print(f"alienfx-studio: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
