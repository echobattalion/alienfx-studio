"""Send profiles to the hardware through alienfx_cli. RAM only: never uses --save."""
import fcntl
import os
import subprocess
import threading
import time
from dataclasses import dataclass

_DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
CLI = (os.environ.get("ALIENFX_CLI")
       or os.path.join(_DATA, "alienfx-studio", "bin", "alienfx_cli"))
KEYBOARD_DEV = int(os.environ.get("ALIENFX_KEYBOARD_DEV", 0))
ELC_DEV = int(os.environ.get("ALIENFX_ELC_DEV", 1))
SETPOWER_ONLY = {19}       # firmware ignores normal writes to this ELC zone
TIMEOUT = 10
WATCH_LOCK = "alienfx-watch.lock"     # held while the game watcher runs
EDITOR_LOCK = "alienfx-editor.lock"   # held while the GUI needs the lights to itself


class DeviceError(Exception):
    pass


@dataclass(frozen=True)
class Frame:
    batch: str
    power: tuple           # ((zone, (r, g, b)), ...)


def _scale(rgb, brightness):
    return tuple(round(c * brightness / 100) for c in rgb)


def build_frame(profile):
    b = profile.brightness
    lines = []
    for i, rgb in enumerate(profile.keyboard_colors()):
        lines.append("{} {} {} {} {}\n".format(KEYBOARD_DEV, i, *_scale(rgb, b)))
    power = []
    for zone, rgb in enumerate(profile.elc_colors()):
        scaled = _scale(rgb, b)
        if zone in SETPOWER_ONLY:
            power.append((zone, scaled))
        else:
            lines.append("{} {} {} {} {}\n".format(ELC_DEV, zone, *scaled))
    return Frame("".join(lines), tuple(power))


def runtime_dir():
    """Per-user directory whose contents do not survive a reboot."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/alienfx-{os.getuid()}"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    return runtime


def _lock_path():
    return os.path.join(runtime_dir(), "alienfx.lock")


def try_lock(name, retries=0):
    """Return an open file holding the named lock, or None. A crash releases it."""
    lock = open(os.path.join(runtime_dir(), name), "w")
    for attempt in range(retries + 1):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock
        except OSError:
            if attempt < retries:
                time.sleep(.02)
    lock.close()
    return None


def lock_held(name):
    """Whether any open file holds the lock. A shared probe cannot block a real holder."""
    with open(os.path.join(runtime_dir(), name), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return False
        except OSError:
            return True


def _run(args, stdin=None):
    try:
        r = subprocess.run([CLI, "--brightness", "255", *args], input=stdin, text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DeviceError(str(e)) from e
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()
        raise DeviceError(detail[-1] if detail else f"{args[0]} exited {r.returncode}")


def delta(frame, last):
    """Smallest frame that turns `last` into `frame`.

    The ELC is slow (about 75 ms per zone, 2 s for setpower), so it gets only
    changed zones. The keyboard is fast and is always written whole if it changed.
    """
    if last is None:
        return frame
    new, old = frame.batch.splitlines(True), last.batch.splitlines(True)
    kb_prefix = f"{KEYBOARD_DEV} "
    kb_new = [l for l in new if l.startswith(kb_prefix)]
    kb_old = [l for l in old if l.startswith(kb_prefix)]
    lines = kb_new if kb_new != kb_old else []
    old_set = set(old)
    lines = lines + [l for l in new if not l.startswith(kb_prefix) and l not in old_set]
    power = tuple(p for p in frame.power if p not in last.power)
    return Frame("".join(lines), power)


def _stamp():
    try:
        return os.stat(_lock_path()).st_mtime_ns
    except OSError:
        return None


def send(frame):
    """Write a frame and mark the lock file so other writers can detect it."""
    with open(_lock_path(), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if frame.batch:
                _run(["setbatch"], frame.batch)
            for zone, (r, g, b) in frame.power:
                c = [str(r), str(g), str(b)]
                _run(["setpower", str(ELC_DEV), str(zone), *c, *c])
        finally:
            os.utime(lock.fileno())
        return os.fstat(lock.fileno()).st_mtime_ns


class LiveWriter:
    """Single worker that always sends the newest frame and drops stale ones."""

    def __init__(self, on_result):
        self._on_result = on_result    # called from worker: (ok, message)
        self._cv = threading.Condition()
        self._pending = None
        self._busy = False
        self._last = None
        self._stamp = None
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, frame):
        with self._cv:
            self._pending = frame
            self._cv.notify_all()

    def _loop(self):
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                if self._pending is None:
                    return
                frame, self._pending = self._pending, None
                self._busy = True
            ok, message = True, None
            if _stamp() != self._stamp:            # another process wrote the lights
                self._last = None
            if frame != self._last:
                start = time.monotonic()
                try:
                    self._stamp = send(delta(frame, self._last))
                    self._last = frame
                    message = f"{(time.monotonic() - start) * 1000:.0f} ms"
                except DeviceError as e:
                    self._last = None
                    ok, message = False, str(e)
                self._on_result(ok, message)
            with self._cv:
                self._busy = False
                self._cv.notify_all()

    def close(self, timeout=TIMEOUT):
        """Finish the newest pending frame, then stop."""
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._thread.join(timeout)
