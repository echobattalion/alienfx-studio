"""File-change notifications for the Qt event loop."""
from PyQt6.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal

from .inotify import Notifier

FALLBACK_MS = 10000       # only used when the system cannot watch a directory


class FileEvents(QObject):
    """Emits `changed` with a set of paths, or None when anything may have changed."""
    changed = pyqtSignal(object)

    def __init__(self, parent, delay=150):
        super().__init__(parent)
        self._pending = set()
        self._flush_timer = QTimer(self, singleShot=True, interval=delay)
        self._flush_timer.timeout.connect(self._flush)
        self._fallback = QTimer(self, interval=FALLBACK_MS)
        self._fallback.timeout.connect(lambda: self.changed.emit(None))
        self._socket = None
        try:
            self._notifier = Notifier()
            self._socket = QSocketNotifier(self._notifier.fileno(),
                                           QSocketNotifier.Type.Read, self)
            self._socket.activated.connect(self._read)
        except OSError:
            self._notifier = None

    def watch(self, directories):
        """Watch exactly these directories."""
        wanted = {str(d) for d in directories}
        if self._notifier is None:
            self._set_fallback(bool(wanted))
            return
        failed = False
        for directory in self._notifier.directories() - wanted:
            self._notifier.remove(directory)
        for directory in wanted - self._notifier.directories():
            try:
                self._notifier.add(directory)
            except OSError:
                failed = True
        self._set_fallback(failed)

    def _set_fallback(self, on):
        if on and not self._fallback.isActive():
            self._fallback.start()
        elif not on:
            self._fallback.stop()

    def _read(self):
        self._pending |= self._notifier.read()
        if self._pending:
            self._flush_timer.start()

    def _flush(self):
        paths, self._pending = self._pending, set()
        self.changed.emit(paths)

    def close(self):
        self._flush_timer.stop()
        self._fallback.stop()
        if self._socket is not None:
            self._socket.setEnabled(False)
            self._socket = None
        if self._notifier is not None:
            self._notifier.close()
            self._notifier = None
