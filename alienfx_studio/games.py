"""The Games screen: choose profiles for game types and individual games."""
from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import device, rules, theme
from .profiles import ProfileError
from .file_events import FileEvents
from .steam import Library

TYPES_WIDTH = 330
COMBO_WIDTH = 168
GAME_TYPE_WIDTH = 140
GAME_PROFILE_WIDTH = 190


class Combo(QComboBox):
    """A dropdown that lets the wheel scroll the page instead of changing its value."""
    popup_closed = pyqtSignal()

    def __init__(self, name, width=COMBO_WIDTH):
        super().__init__()
        self.setAccessibleName(name)
        self.setFixedWidth(width)

    def wheelEvent(self, e):
        e.ignore()

    def hidePopup(self):
        super().hidePopup()
        self.popup_closed.emit()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.TEXT_SECONDARY), 1.6, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 15, self.height() / 2
        p.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))

    def fill(self, entries, value, top_tip=""):
        """Replace the items, given [(text, data)]. The first is the inherited choice."""
        self.blockSignals(True)
        self.clear()
        for text, data in entries:
            self.addItem(text, data)
        if self.count() > 1:
            self.insertSeparator(1)
        self.setCurrentIndex(max(0, self.findData(value)))
        self.setToolTip(top_tip if self.currentIndex() == 0 and top_tip else self.currentText())
        self.blockSignals(False)


class ElidedLabel(QLabel):
    """Shows as much of a long name as fits. The tooltip keeps all of it."""

    def __init__(self, text):
        super().__init__(objectName="rowName")
        self.full_text = text
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAccessibleName(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight,
                                                   self.width()))


class Toggle(QCheckBox):
    """A checkbox drawn as an on/off switch."""
    TRACK = QSize(38, 22)

    def sizeHint(self):
        return QSize(self.TRACK.width() + 10 + self.fontMetrics().horizontalAdvance(self.text()) + 4,
                     max(self.TRACK.height() + 4, self.fontMetrics().height()))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.TRACK.height()
        track = QRectF(1, (self.height() - h) / 2, self.TRACK.width(), h)
        on = self.isChecked()
        p.setPen(QPen(QColor("#FFFFFF"), 1.5) if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT if on else "#3A3641"))
        p.drawRoundedRect(track, h / 2, h / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#16061A" if on else theme.TEXT_SECONDARY))
        knob = track.right() - h / 2 if on else track.left() + h / 2
        p.drawEllipse(QPointF(knob, track.center().y()), h / 2 - 4, h / 2 - 4)
        p.setPen(QColor(theme.TEXT if self.isEnabled() else "#5E5966"))
        area = self.rect().adjusted(self.TRACK.width() + 10, 0, 0, 0)
        p.drawText(area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class DragHandle(QLabel):
    dragged = pyqtSignal(int)      # global y
    dropped = pyqtSignal()
    stepped = pyqtSignal(int)

    def __init__(self, name):
        super().__init__(objectName="dragHandle")
        self.setPixmap(theme.icon("drag", theme.TEXT_SECONDARY, 18).pixmap(18, 18))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(f"Reorder {name}")
        self.setToolTip("Drag to reorder, or focus and press Up or Down")
        self._dragging = False

    def mousePressEvent(self, e):
        self._dragging = e.button() == Qt.MouseButton.LeftButton
        if self._dragging:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.dragged.emit(round(e.globalPosition().y()))

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.dropped.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Up:
            self.stepped.emit(-1)
        elif e.key() == Qt.Key.Key_Down:
            self.stepped.emit(1)
        else:
            super().keyPressEvent(e)


class TypeRow(QFrame):
    def __init__(self, page, game_type):
        super().__init__(objectName="typeRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.page = page
        self.name = game_type.name
        box = QHBoxLayout(self)
        box.setContentsMargins(6, 6, 8, 6)
        box.setSpacing(8)
        self.handle = DragHandle(self.name)
        box.addWidget(self.handle)
        self.label = QLabel(self.name, objectName="rowName")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setToolTip("Steam tags: " + ", ".join(game_type.tags))
        box.addWidget(self.label, 1)
        self.profile = Combo(f"Profile for {self.name}")
        self.profile.activated.connect(
            lambda _: page.set_type_profile(self.name, self.profile.currentData()))
        box.addWidget(self.profile)


class TypeList(QWidget):
    """Rows the user can reorder by dragging a handle or with the arrow keys."""

    def __init__(self, on_order):
        super().__init__()
        self.on_order = on_order
        self.rows = []
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(6)
        self._moved = False

    def set_rows(self, rows):
        for row in self.rows:
            self.box.removeWidget(row)
            row.deleteLater()
        self.rows = rows
        for row in rows:
            self.box.addWidget(row)
            row.handle.dragged.connect(lambda y, r=row: self._drag(r, y))
            row.handle.dropped.connect(self._drop)
            row.handle.stepped.connect(lambda step, r=row: self._step(r, step))

    def _move(self, row, index):
        current = self.rows.index(row)
        if index == current:
            return False
        self.rows.insert(index, self.rows.pop(current))
        self.box.insertWidget(index, row)
        self.box.activate()
        return True

    def _drag(self, row, global_y):
        y = self.mapFromGlobal(QPoint(0, global_y)).y()
        index = sum(1 for other in self.rows
                    if other is not row and other.geometry().center().y() < y)
        self._moved = self._move(row, index) or self._moved

    def _drop(self):
        if self._moved:
            self._moved = False
            self.on_order([row.name for row in self.rows])

    def _step(self, row, step):
        index = self.rows.index(row) + step
        if 0 <= index < len(self.rows) and self._move(row, index):
            self.on_order([r.name for r in self.rows])
            row.handle.setFocus()


class GameRow(QFrame):
    def __init__(self, page, game):
        super().__init__(objectName="gameRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.game = game
        box = QHBoxLayout(self)
        box.setContentsMargins(14, 7, 8, 7)
        box.setSpacing(8)
        self.label = ElidedLabel(game.name)
        self.label.setToolTip(game.name + ("\nSteam tags: " + ", ".join(game.tags)
                                           if game.tags else ""))
        box.addWidget(self.label, 1)
        self.running = QLabel("RUNNING", objectName="running")
        self.running.hide()
        box.addWidget(self.running)
        self.type = Combo(f"Type for {game.name}", GAME_TYPE_WIDTH)
        self.type.activated.connect(
            lambda _: page.set_game_type(self.game.key, self.type.currentData()))
        box.addWidget(self.type)
        self.profile = Combo(f"Profile for {game.name}", GAME_PROFILE_WIDTH)
        self.profile.activated.connect(
            lambda _: page.set_game_profile(self.game.key, self.profile.currentData()))
        box.addWidget(self.profile)
        for combo in (self.type, self.profile):
            combo.popup_closed.connect(lambda: QTimer.singleShot(0, page.check_library))


class GamesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.table = rules.Rules()
        self.library_games = []
        self.game_rows = []
        self.profile_names = []
        self._error = ""
        self._steam_stamp = None
        self.steam_events = FileEvents(self, delay=400)
        self.steam_events.changed.connect(lambda _: self.check_library())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 26, 36, 18)
        outer.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(14)
        self.back_btn = QPushButton(theme.icon("back", theme.TEXT, 16), "Library")
        self.back_btn.setToolTip("Back to Library")
        self.back_btn.clicked.connect(lambda: self.window_.show_library())
        head.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(QLabel("Games", objectName="pageTitle"))
        titles.addWidget(QLabel("Lights change when a game launches and stay until it exits.",
                                objectName="muted"))
        head.addLayout(titles)
        head.addStretch()
        self.auto = Toggle("Automatic switching")
        self.auto.setToolTip("Change profiles automatically while games run")
        self.auto.clicked.connect(self.window_.set_auto_switch)
        head.addWidget(self.auto, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(head)

        self.notice = QLabel(objectName="notice", wordWrap=True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        outer.addWidget(self.notice)

        self.body = QWidget()
        columns = QHBoxLayout(self.body)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        columns.addWidget(self._build_types())
        columns.addWidget(self._build_games(), 1)
        outer.addWidget(self.body, 1)

    def _panel(self, title, hint):
        panel = QFrame(objectName="panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 16, 14, 14)
        box.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(QLabel(title, objectName="sectionTitle"))
        text.addWidget(QLabel(hint, objectName="muted"))
        return panel, box, text

    def _build_types(self):
        panel, box, text = self._panel("Game types", "The first matching type wins.")
        panel.setFixedWidth(TYPES_WIDTH)
        box.addLayout(text)
        self.type_list = TypeList(self.set_type_order)
        box.addWidget(self.type_list)
        box.addStretch()
        return panel

    def _build_games(self):
        panel, box, text = self._panel("Your games", "A game's own profile beats its type.")
        head = QHBoxLayout()
        head.addLayout(text)
        head.addStretch()
        self.search = QLineEdit(objectName="search", placeholderText="Search games")
        self.search.setClearButtonEnabled(True)
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search.setFixedWidth(240)
        self.search.addAction(theme.icon("search", theme.TEXT_SECONDARY, 16),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _: self._filter())
        head.addWidget(self.search)
        box.addLayout(head)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        self.game_box = QVBoxLayout(holder)
        self.game_box.setContentsMargins(0, 0, 8, 0)
        self.game_box.setSpacing(6)
        self.game_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.empty = QLabel("No games found.", objectName="muted")
        self.empty.hide()
        self.game_box.addWidget(self.empty)
        self.scroll.setWidget(holder)
        box.addWidget(self.scroll, 1)
        return panel

    # ---- data ----
    def refresh(self):
        """Reload Steam games, rules, and profiles."""
        library = Library()
        self._steam_stamp = library.stamp()     # read first, so a change during the read is seen
        self._apply_games(library.games())
        self._reload()
        self._filter()

    def _apply_games(self, games):
        """Add, remove, and reorder rows, keeping every row whose game is unchanged."""
        existing = {row.game: row for row in self.game_rows}
        wanted = set(games)
        for game, row in existing.items():
            if game not in wanted:
                self.game_box.removeWidget(row)
                row.hide()
                row.deleteLater()
        self.game_rows = [existing.get(game) or GameRow(self, game) for game in games]
        for index, row in enumerate(self.game_rows):
            self.game_box.insertWidget(index + 1, row)      # index 0 is the empty message
        self.library_games = list(games)

    def _popup_open(self):
        return any(combo.view().isVisible() for row in self.game_rows
                   for combo in (row.type, row.profile))

    def check_library(self):
        """Pick up installs and removals while the screen is open."""
        if not self.isVisible():
            return
        library = Library()
        stamp = library.stamp()
        if stamp == self._steam_stamp or self._popup_open():
            return                              # an open dropdown retries when it closes
        self._steam_stamp = stamp
        self.steam_events.watch(library.watch_dirs())   # a library folder may have been added
        games = library.games()
        if games != self.library_games:
            self._apply_games(games)
            self._sync_rows()
            self._filter()

    def rules_changed(self):
        """Show edits made outside this screen. Its own saves change nothing here."""
        if not self.isVisible() or self._popup_open():
            return
        try:
            same = not self._error and (rules.load(self.window_.store.root).to_dict()
                                        == self.table.to_dict())
        except (ProfileError, OSError):
            same = False
        if not same:
            self._reload()

    def _reload(self):
        self.profile_names = [name for _, name in self.window_.store.list()]
        try:
            self.table = rules.load(self.window_.store.root)
            self._error = ""
        except (ProfileError, OSError) as e:
            self._error = f"Game rules could not be read, so editing is off: {e}"
        self.body.setEnabled(not self._error)
        self.type_list.set_rows([TypeRow(self, t) for t in self.table.types])
        self._sync_rows()
        self.sync_auto_switch()
        self.update_status()

    def _profile_entries(self, none_text, current):
        entries = [(none_text, None)] + [(name, name) for name in self.profile_names]
        if current and current not in self.profile_names:
            entries.append((f"{current} (missing)", current))
        return entries

    def _sync_rows(self):
        default = self.window_.store.active_name()
        default_tip = f"Uses your default profile, {default}"
        for row in self.type_list.rows:
            game_type = self.table.type_named(row.name)
            row.profile.fill(self._profile_entries("Default Profile", game_type.profile),
                             game_type.profile, default_tip)
        for row in self.game_rows:
            game = row.game
            rule = self.table.games.get(game.key, rules.GameRule())
            detected = rules.Rules(types=self.table.types).type_of(game.key, game.tags)
            others = [(t.name, t.name) for t in self.table.types
                      if not detected or t.name != detected.name]
            chosen = self.table.type_named(rule.type) if rule.type else None
            value = chosen.name if chosen and chosen is not detected else None
            row.type.fill([(detected.name if detected else "No type", None), *others], value,
                          "Detected from Steam tags" if detected else "No matching Steam tags")
            effective = self.table.type_of(game.key, game.tags)
            if effective:
                inherited = f"{effective.name}'s Profile"
                uses = effective.profile if effective.profile in self.profile_names else None
                tip = f"{effective.name} uses {uses}" if uses \
                    else f"{effective.name} uses your default profile, {default}"
            else:
                inherited, tip = "Default Profile", default_tip
            row.profile.fill(self._profile_entries(inherited, rule.profile), rule.profile, tip)

    def _filter(self):
        query = self.search.text().strip().lower()
        shown = 0
        for row in self.game_rows:
            visible = query in row.game.name.lower()
            row.setVisible(visible)
            shown += visible
        self.empty.setText("No games match your search." if self.game_rows
                           else "No Steam games found.")
        self.empty.setVisible(not shown)

    def update_status(self):
        watching = device.lock_held(device.WATCH_LOCK)
        running = self.window_.store.running_games() if watching else set()
        for row in self.game_rows:
            row.running.setVisible(row.game.app_id in running)
        if self._error:
            text = self._error
        elif not watching:
            text = "The game watcher isn't running, so profiles won't switch. " \
                   "Run ./install.sh to enable it."
        elif not self.table.enabled:
            text = "Automatic switching is off."
        elif self.window_.game_session:
            profile, game = self.window_.game_session
            text = f"Showing {profile} until {game or 'the game'} exits."
        elif running and device.lock_held(device.EDITOR_LOCK):
            text = "Switching is paused while the editor is open or has unsaved changes."
        else:
            text = ""
        if text != self.notice.text() or bool(text) != self.notice.isVisible():
            self.notice.setText(text)
            self.notice.setVisible(bool(text))

    def sync_auto_switch(self):
        try:
            self.table.enabled = rules.load(self.window_.store.root).enabled
        except (ProfileError, OSError):
            pass
        self.auto.setChecked(self.table.enabled and not self._error)
        self.update_status()

    # ---- edits ----
    def _edit(self, change):
        """Apply a change to the latest rules on disk."""
        try:
            table = rules.load(self.window_.store.root)
            change(table)
            rules.save(self.window_.store.root, table)
            self.table = table
            self._error = ""
        except (ProfileError, OSError) as e:
            self._error = f"Could not save game rules: {e}"
        self._sync_rows()
        self.update_status()

    def set_type_profile(self, name, profile):
        def change(table):
            found = table.type_named(name)
            if found:
                found.profile = profile
        self._edit(change)

    def set_type_order(self, names):
        def change(table):
            order = {name: i for i, name in enumerate(names)}
            table.types.sort(key=lambda t: order.get(t.name, len(order)))
        self._edit(change)

    def set_game_profile(self, key, profile):
        self._edit(lambda table: setattr(table.games.setdefault(key, rules.GameRule()),
                                         "profile", profile))

    def set_game_type(self, key, name):
        self._edit(lambda table: setattr(table.games.setdefault(key, rules.GameRule()),
                                         "type", name))

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self.steam_events.watch(Library().watch_dirs())

    def hideEvent(self, e):
        super().hideEvent(e)
        self.steam_events.watch([])

    def sizeHint(self):
        return QSize(1200, 800)
