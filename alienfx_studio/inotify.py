"""Minimal Linux inotify wrapper, so nothing has to poll for file changes."""
import ctypes
import os
import struct

IN_CLOSE_WRITE = 0x008
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_Q_OVERFLOW = 0x4000
IN_IGNORED = 0x8000
_IN_NONBLOCK = 0o4000
_IN_CLOEXEC = 0o2000000
_MASK = IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
_HEADER = struct.Struct("iIII")

OVERFLOW = ""         # returned by read() when events were lost: assume anything changed

_libc = ctypes.CDLL(None, use_errno=True)
_libc.inotify_init1.argtypes = [ctypes.c_int]
_libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
_libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]


def _check(result, what):
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, f"{what}: {os.strerror(code)}")
    return result


class Notifier:
    """Watches directories. Atomic file replacement shows up as a change to the name."""

    def __init__(self):
        self.fd = _check(_libc.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC), "inotify_init")
        self._dirs = {}       # watch descriptor -> directory

    def fileno(self):
        return self.fd

    def directories(self):
        return set(self._dirs.values())

    def add(self, directory):
        directory = str(directory)
        wd = _check(_libc.inotify_add_watch(self.fd, os.fsencode(directory), _MASK),
                    f"watch {directory}")
        self._dirs[wd] = directory

    def remove(self, directory):
        directory = str(directory)
        for wd, known in list(self._dirs.items()):
            if known == directory:
                del self._dirs[wd]
                _libc.inotify_rm_watch(self.fd, wd)

    def read(self):
        """Return the set of changed paths, without blocking."""
        changed = set()
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return changed
            offset = 0
            while offset < len(data):
                wd, mask, _, size = _HEADER.unpack_from(data, offset)
                offset += _HEADER.size
                name = data[offset:offset + size].split(b"\0", 1)[0]
                offset += size
                if mask & IN_Q_OVERFLOW:
                    changed.add(OVERFLOW)
                elif mask & IN_IGNORED:
                    self._dirs.pop(wd, None)
                elif wd in self._dirs and name:
                    changed.add(os.path.join(self._dirs[wd], os.fsdecode(name)))

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
            self._dirs.clear()
