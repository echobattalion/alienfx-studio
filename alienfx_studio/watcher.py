"""Background watcher: show a game's profile from launch until it exits."""
import os
import select
import signal
import threading
import time

from . import device, inotify, rules, steam
from .profiles import PICKED, ProfileError, Store
from .steam import Library

GAME_SECONDS = 2.5      # a game is running: notice its exit promptly
IDLE_SECONDS = 5.0      # Steam is open: a launch is hidden by the game's loading time
FALLBACK_SECONDS = 10.0  # Steam's state cannot be watched
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
        self.reported = None     # the running set last published to the GUI
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

    def delay(self):
        """Seconds until the next scan, or None to sleep until something changes."""
        if self.games:
            return GAME_SECONDS
        state = steam.steam_state()
        if state == steam.STEAM_RUNNING:
            return IDLE_SECONDS
        return None if state == steam.STEAM_STOPPED else FALLBACK_SECONDS

    def _track(self, running, now):
        if running != self.reported:
            self.store.set_running(running)
            self.reported = set(running)
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
        if self.games and self.rules.enabled:
            return next(iter(self.games.values())), self.store.active_name(), "default profile"
        return None

    def _send(self, name):
        self.frame = device.build_frame(self.store.load(name))
        self.submit(self.frame)

    def resend(self):
        if self.frame is not None:
            self.submit(self.frame)

    def release(self):
        """End a game's lighting and return to the default profile."""
        showing = self.store.showing()
        self.held = None
        if showing is None or showing[1] == PICKED:     # nothing of ours is on the lights
            return
        self.store.clear_showing()
        name = self.store.active_name()
        if showing[0] != name and not self.paused():    # never overwrite the editor
            self._send(name)
            log(f"restored: {name}")

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
            before = self.store.current_name()
            self.store.set_showing(profile, game.key, game.name)
            if before != profile:
                self._send(profile)
            else:
                self.frame = None               # already on the lights
            self.held = (game.key, profile)
            log(f"showing: {profile} for {game.name} ({reason})")


def _sleep(notifier, wake_fd, delay, wake_paths):
    """Wait for the delay, a relevant file change, or a wake-up. None waits indefinitely."""
    deadline = None if delay is None else time.monotonic() + delay
    sources = [wake_fd] + ([notifier] if notifier else [])
    while True:
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            return
        ready = select.select(sources, [], [], left)[0]
        if wake_fd in ready:
            os.read(wake_fd, 4096)
            return
        if notifier in ready:
            changed = notifier.read()
            if inotify.OVERFLOW in changed or changed & wake_paths:
                return


def _open_notifier(store):
    """Watch for Steam starting and for the editor finishing."""
    pid_path = steam.pid_file()
    try:
        notifier = inotify.Notifier()
        notifier.add(device.runtime_dir())
        notifier.add(pid_path.parent)
    except OSError as e:
        log(f"file notifications unavailable, checking every {FALLBACK_SECONDS:.0f}s: {e}")
        return None, set()
    return notifier, {str(pid_path), os.path.join(device.runtime_dir(), device.EDITOR_LOCK)}


def run():
    lock = device.try_lock(device.WATCH_LOCK, retries=5)
    if lock is None:
        log("already running")
        return 0

    stop = threading.Event()
    failed = threading.Event()
    failing = [False]
    wake_read, wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)

    def wake():
        try:
            os.write(wake_write, b"x")
        except BlockingIOError:      # a wake-up is already queued
            pass

    def on_result(ok, message):
        if ok:
            failing[0] = False
            return
        if not failing[0]:
            log(f"lighting write failed: {message}")
        failing[0] = True
        failed.set()
        wake()

    def on_signal(*_):
        stop.set()
        wake()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)

    store = Store()
    notifier, wake_paths = _open_notifier(store)   # before the first scan, so no start is missed
    writer = device.LiveWriter(on_result)
    watcher = Watcher(store, Library(), writer.submit)
    log("watching for Steam games")
    try:
        while not stop.is_set():
            retry = False
            try:
                watcher.tick(time.monotonic())
                if failed.is_set():
                    failed.clear()
                    watcher.resend()
                    retry = True
                delay = watcher.delay()
            except (ProfileError, device.DeviceError, OSError) as e:
                log(f"error: {e}")
                retry = True
                delay = GAME_SECONDS
            if retry:
                delay = min(delay or GAME_SECONDS, GAME_SECONDS)
            elif delay is None and notifier is None:
                delay = FALLBACK_SECONDS
            _sleep(notifier, wake_read, delay, wake_paths)
    finally:
        try:
            watcher.release()
            store.clear_running()
        except (ProfileError, device.DeviceError, OSError) as e:
            log(f"could not restore the default profile: {e}")
        writer.close()
        if notifier:
            notifier.close()
    return 0
