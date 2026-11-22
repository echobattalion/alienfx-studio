"""Main window: the Obsidian editor shell and Library navigation."""
import colorsys
import os
import sys

from PyQt6.QtCore import QEvent, QPointF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFontMetrics, QKeySequence, QPainter, QRadialGradient, QShortcut)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QColorDialog, QHBoxLayout, QInputDialog, QLabel,
                             QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider,
                             QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import device, layout, rules, theme
from .color_dock import ColorDock
from .file_events import FileEvents
from .games import GamesPage
from .inotify import OVERFLOW
from .keyboard_view import KeyboardView
from .library import LibraryPage
from .profiles import PICKED, Profile, ProfileError, Store, to_hex
from .tray import Tray, app_icon

SERVER_NAME = "alienfx-studio"
MAX_UNDO = 100
MAX_RECENT = 10
_STATUS_WIDTH = 340
_STATE_FILES = {"state.json", "alienfx-showing.json", "alienfx-running.json", device.WATCH_LOCK}


class Backdrop(QWidget):
    """The window background, with a soft radial glow behind the keyboard."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.WINDOW))
        center = QPointF(self.width() / 2, self.height() * .42)
        gradient = QRadialGradient(center, max(self.width() * .56, 320))
        gradient.setColorAt(0, QColor(theme.GLOW))
        gradient.setColorAt(.62, QColor(theme.WINDOW_MID))
        gradient.setColorAt(1, QColor(theme.WINDOW))
        p.fillRect(self.rect(), gradient)


class MainWindow(QMainWindow):
    _deviceResult = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        theme.apply(QApplication.instance())
        self.store = Store()
        self.store.drop_stale_showing()
        self.profile = self.store.load(self.store.current_name())
        self.saved = self.profile.to_dict()
        self.game_session = None        # (profile, game) while a game controls the lights
        self._pause_lock = None
        self._restore_pending = False   # a dead watcher left game lighting on the keys
        self._state_stamp = self.store.state_stamp()
        self.undo_stack, self.redo_stack = [], []
        self.recent = []
        self._syncing = False
        self._editing = False
        self._holding = False
        self._color_edited = False
        self.tray = None
        self._deviceResult.connect(self._show_result)
        self.writer = device.LiveWriter(lambda ok, msg: self._deviceResult.emit(ok, msg or ""))
        self._edit_timer = QTimer(self, singleShot=True, interval=500)
        self._edit_timer.timeout.connect(self._end_edit)

        self.setWindowTitle("AlienFX Lighting Studio")
        self.setWindowIcon(app_icon())
        self._build_ui()
        self._refresh_profiles()
        self._sync_controls()
        self._update_selection_ui()
        self.file_events = FileEvents(self)
        self.file_events.changed.connect(self._files_changed)
        self.file_events.watch([self.store.root, device.runtime_dir()])

    # ---- UI construction ----
    def _build_ui(self):
        root = Backdrop()
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.editor_page = self._build_editor()
        self.library = LibraryPage(self)
        self.pages.addWidget(self.library)      # the home screen
        self.pages.addWidget(self.editor_page)
        self.games = GamesPage(self)
        self.pages.addWidget(self.games)
        shell.addWidget(self.pages)
        self.setCentralWidget(root)
        self.setMinimumSize(1040, 517)
        self.resize(1440, 900)

        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.redo)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save)
        QShortcut(QKeySequence("B"), self, self._toggle_paint_mode)
        self._paint_exit = QShortcut(QKeySequence("Esc"), self,
                                     lambda: self.set_paint_mode(False))
        self._paint_exit.setEnabled(False)

    def _build_editor(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(20, 10, 20, 12)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.back_btn = QPushButton(theme.icon("back", theme.TEXT, 16), "Library")
        self.back_btn.setToolTip("Back to Library")
        self.back_btn.clicked.connect(lambda: self.show_library())
        header.addWidget(self.back_btn)
        self.profile_label = QLabel(objectName="appName")
        self.profile_label.setTextFormat(Qt.TextFormat.PlainText)
        self.profile_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Preferred)
        header.addWidget(self.profile_label, 1)
        right = QWidget()
        right.setFixedWidth(230)
        bright = QHBoxLayout(right)
        bright.setContentsMargins(0, 0, 0, 0)
        bright.setSpacing(9)
        sun = QLabel(toolTip="Brightness")
        sun.setPixmap(theme.icon("sun", theme.TEXT, 18).pixmap(18, 18))
        bright.addWidget(sun)
        self.bright = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.bright.setAccessibleName("Brightness")
        self.bright.valueChanged.connect(self._brightness_changed)
        self.bright.sliderPressed.connect(self._hold_edit)
        self.bright.sliderReleased.connect(self._release_edit)
        bright.addWidget(self.bright, 1)
        self.bright_label = QLabel(minimumWidth=38,
                                   alignment=Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
        bright.addWidget(self.bright_label)
        header.addWidget(right)
        outer.addLayout(header)

        self.view = KeyboardView(lambda item: self.profile.color_of(item),
                                 lambda: self.profile.brightness)
        self.view.selectionChanged.connect(self._selection_changed)
        self.view.paintStarted.connect(self._hold_edit)
        self.view.paintItem.connect(self._paint_item)
        self.view.paintFinished.connect(self._release_edit)
        outer.addWidget(self.view, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chips = {}
        for text, ids in [("All", None), ("None", set())] + list(layout.GROUPS.items()):
            chip = QPushButton(text, objectName="chip", checkable=True)
            chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            chip.clicked.connect(lambda _=False, s=ids: self._chip_clicked(s))
            chips.addWidget(chip)
            self.chips[text] = chip
        chips.addStretch()
        self.status = QLabel("Live: ready", objectName="muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setMaximumWidth(_STATUS_WIDTH)
        chips.addWidget(self.status)
        outer.addLayout(chips)

        self.picker = ColorDock()
        self.picker.currentColorChanged.connect(self._picker_changed)
        self.picker.interactionStarted.connect(self._hold_edit)
        self.picker.interactionFinished.connect(self._release_edit)
        self.picker.paint_btn.toggled.connect(self.set_paint_mode)
        self.picker.fill_btn.clicked.connect(lambda: self._paint(self._picker_hex()))
        self.picker.undo_btn.clicked.connect(lambda: self.undo())
        self.picker.save_btn.clicked.connect(lambda: self.save())
        self.save_btn = self.picker.save_btn

        menu = QMenu(self.picker.more_btn)
        menu.addAction("Save as…", self.save_as)
        self.revert_action = menu.addAction("Revert", self.revert)
        menu.addAction("Rename…", self.rename)
        menu.addAction("Delete…", self.delete)
        menu.addSeparator()
        gradient = menu.addMenu("Touchpad gradient")
        gradient.addAction("Rainbow", self._rainbow)
        gradient.addAction("Two colors…", self._two_color)
        self.picker.more_btn.setMenu(menu)

        dock_row = QHBoxLayout()
        dock_row.addStretch()
        dock_row.addWidget(self.picker, 100)
        dock_row.addStretch()
        outer.addLayout(dock_row)
        return page

    # ---- navigation ----
    def show_editor(self):
        self.pages.setCurrentWidget(self.editor_page)
        self._update_pause()

    def show_library(self):
        self.set_paint_mode(False)
        self.pages.setCurrentWidget(self.library)
        self._update_pause()

    def show_games(self):
        self.set_paint_mode(False)
        self.pages.setCurrentWidget(self.games)
        self._update_pause()

    def open_games(self):
        self.show_games()
        self.present()

    def open_editor(self):
        self.show_editor()
        self.present()

    # ---- state helpers ----
    @property
    def dirty(self):
        return self.profile.to_dict() != self.saved

    def _picker_hex(self):
        return to_hex(self.picker.currentColor().getRgb()[:3])

    def _read_game_session(self):
        showing = self.store.showing()
        in_game = showing is not None and showing[1] != PICKED
        self.game_session = (showing[0], self.store.showing_game()) if in_game else None

    def _refresh_profiles(self):
        self._read_game_session()
        self.library.invalidate()
        self._update_title()
        if self.tray:
            self.tray.rebuild()

    def _update_title(self):
        dirty = self.dirty
        mark = "*" if dirty else ""
        self.setWindowTitle(f"{self.profile.name}{mark} - AlienFX Lighting Studio")
        self.profile_label.setText(self.profile.name)
        self.profile_label.setToolTip(self.profile.name)
        self.picker.set_dirty(dirty)
        self.revert_action.setEnabled(dirty)
        self.library.sync_active()
        self._update_pause()

    def _sync_controls(self):
        self._syncing = True
        self.bright.setValue(self.profile.brightness)
        self.bright_label.setText(f"{self.profile.brightness}%")
        self._syncing = False
        self.view.update()
        self._update_title()

    def _push_live(self):
        self.writer.submit(device.build_frame(self.profile))

    def _show_result(self, ok, message):
        text = f"Live: OK ({message})" if ok else f"Live: failed: {message}"
        metrics = QFontMetrics(self.status.font())
        self.status.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, _STATUS_WIDTH))
        self.status.setToolTip(text)
        self.status.setStyleSheet("" if ok else f"color: {theme.DANGER};")

    def _begin_edit(self):
        if not self._editing:
            self.undo_stack.append(self.profile.to_dict())
            del self.undo_stack[:-MAX_UNDO]
            self.redo_stack.clear()
            self._editing = True
        if self._holding:
            self._edit_timer.stop()
        else:
            self._edit_timer.start()

    def _end_edit(self):
        self._editing = False
        if self._color_edited and (self.view.selected or self.view.paint_mode):
            self._remember(self._picker_hex())
        self._color_edited = False

    def _hold_edit(self):
        """A drag started: keep it as one undo step, however long it pauses."""
        self._holding = True
        self._edit_timer.stop()

    def _release_edit(self):
        self._holding = False
        if self._editing:
            self._end_edit()

    def _changed(self):
        self.view.update()
        self._update_title()
        self._push_live()

    # ---- editing ----
    def _selection_changed(self):
        self._end_edit_silently()
        self._update_selection_ui()
        if not self.view.selected:
            return
        first = next(i.id for i in self.view.items if i.id in self.view.selected)
        self._syncing = True
        self.picker.setCurrentColor(QColor(self.profile.color_of(first)))
        self._syncing = False

    def _update_selection_ui(self):
        if self.view.paint_mode:
            for chip in self.chips.values():
                chip.setChecked(False)
            self.picker.set_selection_label("Painting · click keys")
            return
        selected = self.view.selected
        everything = self.view.all_ids()
        matched = None
        for text, chip in self.chips.items():
            ids = everything if text == "All" else set() if text == "None" \
                else layout.GROUPS[text]
            same = selected == ids
            chip.setChecked(same)
            if same and selected and matched is None:
                matched = text
        count = len(selected)
        if not count:
            label = "Select keys to color"
        elif matched:
            label = f"{matched} · {count} lights"
        elif count == 1:
            label = next(i.label for i in self.view.items if i.id in selected)
        else:
            label = f"{count} lights"
        self.picker.set_selection_label(label)

    def _end_edit_silently(self):
        self._edit_timer.stop()
        self._editing = False
        self._color_edited = False

    def _picker_changed(self, _):
        if not self._syncing:
            self._paint(self._picker_hex())

    def _paint(self, value):
        if not self.view.selected:
            return
        if all(self.profile.color_of(i) == value for i in self.view.selected):
            return
        self._begin_edit()
        self._color_edited = True
        for item in self.view.selected:
            self.profile.set_color(item, value)
        self._changed()

    # ---- paint mode ----
    def set_paint_mode(self, on):
        if on == self.view.paint_mode:
            return
        if self._editing and not self._holding:
            self._end_edit()
        self.view.set_paint_mode(on)
        self.picker.paint_btn.setChecked(on)
        self.picker.fill_btn.setEnabled(not on)
        self.chips["None"].setEnabled(not on)
        self._paint_exit.setEnabled(on)
        self._update_selection_ui()

    def _toggle_paint_mode(self):
        if self.pages.currentWidget() is self.editor_page:
            self.set_paint_mode(not self.view.paint_mode)

    def _paint_item(self, item):
        value = self._picker_hex()
        if self.profile.color_of(item) == value:
            return
        self._begin_edit()
        self._color_edited = True
        self.profile.set_color(item, value)
        self._changed()

    def _chip_clicked(self, ids):
        targets = self.view.all_ids() if ids is None else ids
        if not self.view.paint_mode:
            self.view.select(targets)
            return
        value = self._picker_hex()               # in paint mode a chip fills its group
        changed = [i for i in targets if self.profile.color_of(i) != value]
        if changed:
            self._end_edit_silently()
            self._begin_edit()
            for item in changed:
                self.profile.set_color(item, value)
            self._changed()
            self._remember(value)
            self._end_edit_silently()
        self._update_selection_ui()

    def _brightness_changed(self, value):
        self.bright_label.setText(f"{value}%")
        if self._syncing or value == self.profile.brightness:
            return
        self._begin_edit()
        self.profile.brightness = value
        self._changed()

    def _remember(self, value):
        if value in self.recent:
            self.recent.remove(value)
        self.recent.insert(0, value)
        del self.recent[MAX_RECENT:]
        self.picker.set_recent(self.recent)

    def _set_touchpad(self, colors):
        self._end_edit_silently()
        self._begin_edit()
        for zone, value in zip(layout.TOUCHPAD_ZONES, colors):
            self.profile.zones[zone] = value
        self._changed()
        self._end_edit_silently()

    def _rainbow(self):
        n = len(layout.TOUCHPAD_ZONES)
        self._set_touchpad([to_hex(tuple(round(c * 255) for c in colorsys.hsv_to_rgb(
            (i / (n - 1)) * 280 / 360, 1, 1))) for i in range(n)])

    def _two_color(self):
        a = QColorDialog.getColor(self.picker.currentColor(), self, "Gradient start")
        if not a.isValid():
            return
        b = QColorDialog.getColor(a, self, "Gradient end")
        if not b.isValid():
            return
        n = len(layout.TOUCHPAD_ZONES)
        ca, cb = a.getRgb()[:3], b.getRgb()[:3]
        self._set_touchpad([to_hex(tuple(round(x + (y - x) * i / (n - 1))
                                         for x, y in zip(ca, cb))) for i in range(n)])

    def _restore(self, snapshot):
        self._end_edit_silently()
        self.profile = Profile.from_dict(snapshot)
        self._sync_controls()
        self._selection_changed()
        self._push_live()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.profile.to_dict())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.profile.to_dict())
            self._restore(self.redo_stack.pop())

    # ---- profiles ----
    def _guard(self, fn, *args):
        try:
            result = fn(*args)
            return True if result is None else result
        except (ProfileError, OSError) as e:
            QMessageBox.warning(self, "AlienFX Lighting Studio", str(e))
            return None

    def _confirm_unsaved(self):
        """Return True when it is safe to leave the current edits."""
        if not self.dirty:
            return True
        self.present()
        answer = QMessageBox.question(
            self, "Unsaved changes", f"Save changes to {self.profile.name}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            self.save()
            return not self.dirty
        return answer == QMessageBox.StandardButton.Discard

    def _load(self, profile, choose=True):
        """Show a profile. With choose=False, the watcher already put it on the lights."""
        self.profile = profile
        self.saved = profile.to_dict()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._end_edit_silently()
        if choose:
            self.store.choose(profile.name)
        self._refresh_profiles()
        self._sync_controls()
        self._selection_changed()
        if choose:
            self._push_live()

    def switch_profile(self, name):
        if name == self.profile.name or not self._confirm_unsaved():
            self._refresh_profiles()
            return
        profile = self._guard(self.store.load, name)
        if profile:
            self._load(profile)
        else:
            self._refresh_profiles()

    def edit_profile(self, name):
        """Apply a profile if needed, then open it in the editor."""
        if name != self.profile.name:
            self.switch_profile(name)
        if name == self.profile.name:
            self.show_editor()

    def save(self):
        if self._guard(self.store.save, self.profile):
            self.saved = self.profile.to_dict()
            self.store.choose(self.profile.name)
            self._refresh_profiles()

    def _ask_name(self, title, current):
        name, ok = QInputDialog.getText(self, title, "Profile name:", text=current)
        return name.strip() if ok and name.strip() else None

    def save_as(self):
        name = self._ask_name("Save profile as", f"{self.profile.name} copy")
        if not name:
            return
        if self.store.exists(name):
            answer = QMessageBox.question(self, "Overwrite profile",
                                          f"A profile matching {name!r} exists. Overwrite it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        copy = self.profile.copy()
        copy.name = name
        if self._guard(self.store.save, copy):
            self.profile = copy
            self.saved = copy.to_dict()
            self.store.choose(name)
            self._refresh_profiles()

    def rename(self):
        if self.dirty and not self._confirm_unsaved():
            return
        name = self._ask_name("Rename profile", self.profile.name)
        if not name or name == self.profile.name:
            return
        renamed = self._guard(self.store.rename, self.saved["name"], name)
        if renamed:
            self._load(renamed)

    def rename_profile(self, name):
        """Rename any profile. A non-active one leaves the current edits alone."""
        if name == self.profile.name:
            self.rename()
            return
        new = self._ask_name("Rename profile", name)
        if new and new != name and self._guard(self.store.rename, name, new):
            self._refresh_profiles()

    def delete(self):
        answer = QMessageBox.question(self, "Delete profile",
                                      f"Delete {self.profile.name}? This cannot be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(self.profile.name)
            self._load(self.store.load(self.store.current_name()))
        except (ProfileError, OSError) as e:
            QMessageBox.warning(self, "AlienFX Lighting Studio", str(e))

    def delete_profile(self, name):
        """Delete any profile. A non-active one leaves the current edits alone."""
        if name == self.profile.name:
            self.delete()
            return
        answer = QMessageBox.question(self, "Delete profile",
                                      f"Delete {name}? This cannot be undone.")
        if answer == QMessageBox.StandardButton.Yes and self._guard(self.store.delete, name):
            self._refresh_profiles()

    def revert(self):
        self._restore(self.saved)
        self.undo_stack.clear()
        self.redo_stack.clear()

    # ---- game sessions ----
    def _update_pause(self):
        """Hold the watcher off while the editor is open or has unsaved edits."""
        paused = self.dirty or (self.isVisible() and not self.isMinimized()
                                and self.pages.currentWidget() is self.editor_page)
        if paused and self._pause_lock is None:
            self._pause_lock = device.try_lock(device.EDITOR_LOCK)
        elif not paused and self._pause_lock is not None:
            self._pause_lock.close()
            self._pause_lock = None
            QTimer.singleShot(0, self._poll_game_state)     # catch up on what editing held back

    def _files_changed(self, paths):
        """React to the watcher, the command line, or a crash. None means anything changed."""
        names = None if paths is None or OVERFLOW in paths \
            else {os.path.basename(path) for path in paths}
        if names is None or names & _STATE_FILES:
            self._poll_game_state()
            self.games.update_status()
        if names is None or "rules.json" in names:
            self.games.rules_changed()

    def _poll_game_state(self):
        self._update_pause()
        self._restore_pending |= self.store.drop_stale_showing()
        stamp = self.store.state_stamp()
        if stamp == self._state_stamp and not self._restore_pending:
            return
        self._read_game_session()
        self.library.sync_active()
        if self._pause_lock is not None:
            return                      # handled when editing ends, because the stamp stays old
        self._state_stamp = stamp
        try:
            name = self.store.current_name()
            if name != self.profile.name:
                self._load(self.store.load(name), choose=False)
            else:
                self.library.invalidate()
        except (ProfileError, OSError) as e:
            print(f"alienfx-studio: {e}", file=sys.stderr)
            return
        if self._restore_pending:
            self._restore_pending = False
            self._push_live()

    def set_default_profile(self, name):
        """Change the default profile. The lights stay as they are."""
        self._guard(self.store.set_default, name)
        self._refresh_profiles()

    def auto_switch(self):
        try:
            return rules.load(self.store.root).enabled
        except (ProfileError, OSError):
            return False

    def set_auto_switch(self, on):
        try:
            table = rules.load(self.store.root)
            table.enabled = bool(on)
            rules.save(self.store.root, table)
        except (ProfileError, OSError) as e:
            QMessageBox.warning(self, "AlienFX Lighting Studio", str(e))
        self.games.sync_auto_switch()

    # ---- window lifecycle ----
    def showEvent(self, event):
        super().showEvent(event)
        self._update_pause()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._update_pause()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_pause()

    def present(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.tray and self.tray.isVisible():
            self.hide()
            event.ignore()
        elif self.request_quit(close=False):
            event.accept()
        else:
            event.ignore()

    def request_quit(self, close=True):
        if not self._confirm_unsaved():
            return False
        if self.dirty:               # discarded: put hardware back to the saved look
            self._restore(self.saved)
        self.writer.close()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()
        return True


def run(start_hidden=False):
    app = QApplication(sys.argv)
    app.setApplicationName("alienfx-studio")
    app.setDesktopFileName("alienfx-studio")
    app.setQuitOnLastWindowClosed(False)

    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(200):      # already running: raise it
        if not start_hidden:
            probe.write(b"show")
            probe.waitForBytesWritten(200)
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    window = MainWindow()

    def on_connection():
        sock = server.nextPendingConnection()
        sock.readyRead.connect(window.present)
        sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(on_connection)

    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = Tray(window, window.request_quit)
        window.tray.show()
    if not start_hidden or not window.tray:
        window.present()
    return app.exec()
