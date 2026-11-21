"""Main editor window."""
import colorsys
import sys

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QColorDialog, QComboBox, QHBoxLayout,
                             QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QSizePolicy, QSlider, QSystemTrayIcon,
                             QToolButton, QVBoxLayout, QWidget)

from . import device, layout
from .profiles import Profile, ProfileError, Store, to_hex
from .tray import Tray, app_icon

SERVER_NAME = "alienfx-studio"
MAX_UNDO = 100
MAX_RECENT = 10


class LightView(QWidget):
    """Draws keys and ELC zones; handles click, Ctrl-click and rubber-band select."""
    selectionChanged = pyqtSignal()

    def __init__(self, color_of):
        super().__init__()
        self.items = layout.build_items()
        self.color_of = color_of
        self.selected = set()
        self._origin = None
        self._band = None
        self._base = set()
        self.setMinimumSize(760, 370)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _unit(self):
        return min((self.width() - 16) / layout.WIDTH, (self.height() - 16) / layout.HEIGHT)

    def _rect(self, item):
        u = self._unit()
        return QRectF(8 + item.x * u + 1.5, 8 + item.y * u + 1.5,
                      item.w * u - 3, item.h * u - 3)

    def _at(self, pos):
        return next((i for i in self.items if i.enabled and self._rect(i).contains(pos)), None)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        u = self._unit()
        font = QFont(self.font())
        font.setPixelSize(max(9, int(u * .24)))
        p.setFont(font)
        p.setPen(pal.placeholderText().color())
        for title, x in layout.SECTIONS:
            p.drawText(QPointF(8 + x * u + 2, 8 + layout.SECTION_Y * u + u * .25), title)
        for item in self.items:
            r = self._rect(item)
            if not item.enabled:
                p.setPen(QPen(pal.mid().color(), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r, 4, 4)
                continue
            color = QColor(self.color_of(item.id))
            p.setBrush(color)
            if item.id in self.selected:
                p.setPen(QPen(pal.highlightedText().color(), 1))
                p.drawRoundedRect(r, 4, 4)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(pal.highlight().color(), 3))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 5, 5)
            else:
                p.setPen(QPen(color.darker(160), 1))
                p.drawRoundedRect(r, 4, 4)
            luma = .299 * color.red() + .587 * color.green() + .114 * color.blue()
            p.setPen(QColor("#000000" if luma > 128 else "#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, item.label)
        if self._band:
            p.setPen(QPen(pal.highlight().color(), 1, Qt.PenStyle.DashLine))
            band = QColor(pal.highlight().color())
            band.setAlpha(50)
            p.setBrush(band)
            p.drawRect(self._band)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self._origin = e.position()
        self._base = set(self.selected) if ctrl else set()
        hit = self._at(e.position())
        if hit:
            self.selected = self._base ^ {hit.id}
        else:
            self.selected = set(self._base)
        self.selectionChanged.emit()
        self.update()

    def mouseMoveEvent(self, e):
        if self._origin is None:
            return
        if (e.position() - self._origin).manhattanLength() < 5:
            return
        self._band = QRectF(self._origin, e.position()).normalized()
        self.selected = self._base | {i.id for i in self.items
                                      if i.enabled and self._band.intersects(self._rect(i))}
        self.selectionChanged.emit()
        self.update()

    def mouseReleaseEvent(self, _):
        self._origin = None
        self._band = None
        self.update()

    def select(self, ids):
        self.selected = set(ids)
        self.selectionChanged.emit()
        self.update()

    def all_ids(self):
        return {i.id for i in self.items if i.enabled}

    def sizeHint(self):
        return QSize(900, 430)


class MainWindow(QMainWindow):
    _deviceResult = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.store = Store()
        self.profile = self.store.load(self.store.active_name())
        self.saved = self.profile.to_dict()
        self.undo_stack, self.redo_stack = [], []
        self.recent = []
        self._syncing = False
        self._editing = False
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

    # ---- UI construction ----
    def _build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)

        top = QHBoxLayout()
        top.addWidget(QLabel("Profile:"))
        self.combo = QComboBox(minimumWidth=180)
        self.combo.activated.connect(self._combo_activated)
        top.addWidget(self.combo)
        for text, fn in (("Save", self.save), ("Save as…", self.save_as), ("Revert", self.revert)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, f=fn: f())
            top.addWidget(button)
            if text == "Save":
                self.save_btn = button
            elif text == "Revert":
                self.revert_btn = button
        more = QToolButton(text="More", popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("Rename…", self.rename)
        menu.addAction("Delete…", self.delete)
        more.setMenu(menu)
        top.addWidget(more)
        top.addStretch()
        top.addWidget(QLabel("Brightness"))
        self.bright = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.bright.setFixedWidth(160)
        self.bright.valueChanged.connect(self._brightness_changed)
        top.addWidget(self.bright)
        self.bright_label = QLabel(minimumWidth=40)
        top.addWidget(self.bright_label)
        outer.addLayout(top)

        mid = QHBoxLayout()
        self.view = LightView(lambda item: self.profile.color_of(item))
        self.view.selectionChanged.connect(self._selection_changed)
        mid.addWidget(self.view, 1)

        side = QVBoxLayout()
        self.picker = QColorDialog(self)
        self.picker.setOptions(QColorDialog.ColorDialogOption.NoButtons
                               | QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        self.picker.currentColorChanged.connect(self._picker_changed)
        side.addWidget(self.picker)
        side.addWidget(QLabel("Recent colors"))
        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(3)
        self.recent_row.addStretch()
        side.addLayout(self.recent_row)
        tools = QHBoxLayout()
        fill = QPushButton("Fill selection")
        fill.clicked.connect(lambda: self._paint(self._picker_hex()))
        tools.addWidget(fill)
        grad = QToolButton(text="Touchpad gradient",
                           popupMode=QToolButton.ToolButtonPopupMode.InstantPopup)
        grad_menu = QMenu(grad)
        grad_menu.addAction("Rainbow", self._rainbow)
        grad_menu.addAction("Two colors…", self._two_color)
        grad.setMenu(grad_menu)
        tools.addWidget(grad)
        side.addLayout(tools)
        side.addStretch()
        mid.addLayout(side)
        outer.addLayout(mid, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Select:"))
        for text, ids in [("All", None), ("None", set())] + list(layout.GROUPS.items()):
            button = QPushButton(text)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, s=ids: self.view.select(
                self.view.all_ids() if s is None else s))
            bottom.addWidget(button)
        bottom.addStretch()
        self.status = QLabel("Live: ready")
        bottom.addWidget(self.status)
        outer.addLayout(bottom)
        self.setCentralWidget(root)

        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.redo)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save)

    # ---- state helpers ----
    @property
    def dirty(self):
        return self.profile.to_dict() != self.saved

    def _picker_hex(self):
        return to_hex(self.picker.currentColor().getRgb()[:3])

    def _refresh_profiles(self):
        self.combo.clear()
        for _, name in self.store.list():
            self.combo.addItem(name)
        self.combo.setCurrentText(self.profile.name)
        self._update_title()
        if self.tray:
            self.tray.rebuild()

    def _update_title(self):
        mark = "*" if self.dirty else ""
        self.setWindowTitle(f"{self.profile.name}{mark} - AlienFX Lighting Studio")
        i = self.combo.findText(self.profile.name)
        if i >= 0:
            self.combo.setItemText(i, self.profile.name)
        self.save_btn.setText("Save*" if self.dirty else "Save")
        self.revert_btn.setEnabled(self.dirty)

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
        self.status.setText(f"Live: OK ({message})" if ok else f"Live: failed: {message}")
        self.status.setStyleSheet("" if ok else "color: #d33;")

    def _begin_edit(self):
        if not self._editing:
            self.undo_stack.append(self.profile.to_dict())
            del self.undo_stack[:-MAX_UNDO]
            self.redo_stack.clear()
            self._editing = True
        self._edit_timer.start()

    def _end_edit(self):
        self._editing = False
        if self.view.selected:
            self._remember(self._picker_hex())

    def _changed(self):
        self.view.update()
        self._update_title()
        self._push_live()

    # ---- editing ----
    def _selection_changed(self):
        self._end_edit_silently()
        if not self.view.selected:
            return
        first = next(i.id for i in self.view.items if i.id in self.view.selected)
        self._syncing = True
        self.picker.setCurrentColor(QColor(self.profile.color_of(first)))
        self._syncing = False

    def _end_edit_silently(self):
        self._edit_timer.stop()
        self._editing = False

    def _picker_changed(self, _):
        if not self._syncing:
            self._paint(self._picker_hex())

    def _paint(self, value):
        if not self.view.selected:
            return
        if all(self.profile.color_of(i) == value for i in self.view.selected):
            return
        self._begin_edit()
        for item in self.view.selected:
            self.profile.set_color(item, value)
        self._changed()

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
        while self.recent_row.count() > 1:
            widget = self.recent_row.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        for i, color in enumerate(self.recent):
            swatch = QPushButton(toolTip=color)
            swatch.setFixedSize(24, 24)
            swatch.setStyleSheet(f"background: {color}; border: 1px solid #555; border-radius: 3px;")
            swatch.clicked.connect(lambda _=False, c=color: self.picker.setCurrentColor(QColor(c)))
            self.recent_row.insertWidget(i, swatch)

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

    def _load(self, profile):
        self.profile = profile
        self.saved = profile.to_dict()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._end_edit_silently()
        self.store.set_active(profile.name)
        self._refresh_profiles()
        self._sync_controls()
        self._selection_changed()
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

    def _combo_activated(self, _):
        self.switch_profile(self.combo.currentText())

    def save(self):
        if self._guard(self.store.save, self.profile):
            self.saved = self.profile.to_dict()
            self.store.set_active(self.profile.name)
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
            self.store.set_active(name)
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

    def delete(self):
        answer = QMessageBox.question(self, "Delete profile",
                                      f"Delete {self.profile.name}? This cannot be undone.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(self.profile.name)
            remaining = self.store.list()
            self._load(self.store.load(remaining[0][1]))
        except (ProfileError, OSError) as e:
            QMessageBox.warning(self, "AlienFX Lighting Studio", str(e))

    def revert(self):
        self._restore(self.saved)
        self.undo_stack.clear()
        self.redo_stack.clear()

    # ---- window lifecycle ----
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
