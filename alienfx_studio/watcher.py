"""Background watcher: show a game's profile from launch until it exits."""
import os
import signal
import threading
import time

from . import device, rules
from .profiles import ProfileError, Store
from .steam import Library

POLL_SECONDS = 2.5
EXIT_GRACE = 5.0     # a game that restarts itself should not flash the lights


def log(message):
    print(f"alienfx-watch: {message}", flush=True)


def running_steam_apps(proc="/proc"):
    """App IDs of running Steam games, from each game's `reaper` session process."""
    found = set()
    try:
        entries = os.listdir(proc)
    except OSError:
        return found
    for pid in entries:
        if not pid.isdigit():
            continue
        try:
            with open(f"{proc}/{pid}/comm") as f:
                if f.read().strip() != "reaper":
                    continue
            with open(f"{proc}/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:              # processes exit while we scan
            continue
        if b"SteamLaunch" not in args:
            continue
        for arg in args:
            if arg.startswith(b"AppId=") and arg[6:].isdigit() and int(arg[6:]) > 0:
                found.add(int(arg[6:]))
                break
    return found


class Watcher:
    def __init__(self, store, library, submit, scan=running_steam_apps,
                 paused=lambda: device.lock_held(device.EDITOR_LOCK)):
        self.store = store
        self.library = library
        self.submit = submit
        self.scan = scan
        self.paused = paused
        self.games = {}          # launch order: app_id -> Game
        self.missing = {}        # app_id -> when it was first seen gone
        self.held = None         # (source, profile) currently shown for a game
        self.user_choice = None  # app_id whose session the user took over
        self.frame = None
        self.rules = rules.Rules()
        self._rules_stamp = None
        self._reload_rules()

    def _reload_rules(self):
        try:
            stamp = rules.path(self.store.root).stat().st_mtime_ns
        except OSError:
            stamp = None
        if stamp == self._rules_stamp:
            return
        self._rules_stamp = stamp
        try:
            self.rules = rules.load(self.store.root)
        except (rules.RulesError, OSError) as e:
            log(f"keeping previous rules: {e}")

    def _track(self, running, now):
        for app_id in sorted(running - set(self.games)):
            self.games[app_id] = self.library.game(app_id)
            log(f"started: {self.games[app_id].name} ({app_id})")
        for app_id in running:
            self.missing.pop(app_id, None)
        for app_id in set(self.games) - running:
            gone_at = self.missing.setdefault(app_id, now)
            if now - gone_at >= EXIT_GRACE:
                log(f"exited: {self.games[app_id].name} ({app_id})")
                del self.games[app_id], self.missing[app_id]
                if self.user_choice == app_id:
                    self.user_choice = None

    def _target(self):
        """The first running game, in launch order, that has a profile."""
        for game in self.games.values():
            found = self.rules.profile_for(game.key, game.tags, self.store.exists)
            if found:
                return game, *found
        return None

    def _send(self, name):
        self.frame = device.build_frame(self.store.load(name))
        self.submit(self.frame)

    def resend(self):
        if self.frame is not None:
            self.submit(self.frame)

    def release(self):
        """Return to the normal profile."""
        had_temporary = self.store.showing() is not None
        self.store.clear_showing()
        if (self.held or had_temporary) and not self.paused():   # never overwrite the editor
            name = self.store.active_name()
            self._send(name)
            log(f"restored: {name}")
        self.held = None

    def tick(self, now):
        self._reload_rules()
        self._track(self.scan(), now)
        if self.paused():
            return

        if self.held:
            showing = self.store.showing()
            if showing is None or showing[1] != self.held[0]:     # the user picked a profile
                owner = int(self.held[0].split(":", 1)[1])
                self.held = None
                if owner in self.games:
                    self.user_choice = owner
                    log(f"manual profile choice: leaving {self.games[owner].name} alone")
        if self.user_choice is not None:
            return

        target = self._target()
        if target is None:
            self.release()
            return
        game, profile, reason = target
        if self.held != (game.key, profile):
            self.store.set_showing(profile, game.key, game.name)
            self._send(profile)
            self.held = (game.key, profile)
            log(f"showing: {profile} for {game.name} ({reason})")


def run():
    lock = device.try_lock(device.WATCH_LOCK, retries=5)
    if lock is None:
        log("already running")
        return 0

    stop = threading.Event()
    failed = threading.Event()
    failing = [False]

    def on_result(ok, message):
        if ok:
            failing[0] = False
            return
        if not failing[0]:
            log(f"lighting write failed: {message}")
        failing[0] = True
        failed.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    writer = device.LiveWriter(on_result)
    watcher = Watcher(Store(), Library(), writer.submit)
    log("watching for Steam games")
    try:
        while not stop.is_set():
            try:
                watcher.tick(time.monotonic())
                if failed.is_set():
                    failed.clear()
                    watcher.resend()
            except (ProfileError, device.DeviceError, OSError) as e:
                log(f"error: {e}")
            stop.wait(POLL_SECONDS)
    finally:
        try:
            watcher.release()
        except (ProfileError, device.DeviceError, OSError) as e:
            log(f"could not restore the normal profile: {e}")
        writer.close()
    return 0
