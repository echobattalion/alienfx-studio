"""The Library screen: a gallery of the user's real profiles."""
import sys
from collections import Counter

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
                             QWidget)

from . import layout, theme
from .keyboard_view import KeyboardView, led_state
from .profiles import ProfileError

CARD_MIN_WIDTH = 420
CARD_HEIGHT = 310
GRID_GAP = 20


def dominant_color(profile):
    """The most common lit key color, used for the soft glow behind a preview."""
    counts = Counter()
    for item in layout.build_items():
        if item.enabled and item.kind != "zone":
            norm, level = led_state(profile.color_of(item.id), profile.brightness)
            if level:
                counts[norm] += 1
    return QColor(*counts.most_common(1)[0][0]) if counts else None


class PreviewPane(QWidget):
    """A read-only keyboard over a soft glow. It never writes to hardware."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.glow = dominant_color(profile)
        self.view = KeyboardView(lambda item: self.profile.color_of(item),
                                 lambda: self.profile.brightness, read_only=True)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 12)
        box.addWidget(self.view)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_profile(self, profile):
        self.profile = profile
        self.glow = dominant_color(profile)
        self.update()
        self.view.update()

    def paintEvent(self, _):
        if self.glow is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        gradient = QRadialGradient(center, self.width() * .52)
        inner = QColor(self.glow)
        inner.setAlpha(46)
        outer = QColor(self.glow)
        outer.setAlpha(0)
        gradient.setColorAt(0, inner)
        gradient.setColorAt(1, outer)
        p.fillRect(self.rect().adjusted(2, 2, -2, 0), gradient)


class DefaultCheck(QCheckBox):
    """A checkbox whose box stays visible on the dark card."""
    BOX = 16

    def sizeHint(self):
        return QSize(self.BOX + 7 + self.fontMetrics().horizontalAdvance(self.text()) + 4,
                     max(self.BOX + 4, self.fontMetrics().height()))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1, (self.height() - self.BOX) / 2, self.BOX, self.BOX)
        on = self.isChecked()
        edge = "#FFFFFF" if self.hasFocus() else theme.ACCENT if on else "#77717F"
        p.setPen(QPen(QColor(edge), 1.4))
        p.setBrush(QColor(theme.ACCENT) if on else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box, 4, 4)
        if on:
            p.save()
            p.translate(box.left() + 2, box.top() + 2)
            p.scale((self.BOX - 4) / 24, (self.BOX - 4) / 24)
            p.fillPath(theme.icon_path("check"), QColor("#16061A"))
            p.restore()
        p.setPen(QColor(theme.TEXT))
        area = self.rect().adjusted(self.BOX + 7, 0, 0, 0)
        p.drawText(area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class ProfileCard(QFrame):
    def __init__(self, window, profile, active, is_default=False):
        super().__init__()
        self.window_ = window
        self.profile = profile
        self.name = profile.name
        self.active = active
        self.is_default = is_default
        self._pressed = False
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(f"Apply {self.name}" if not active else f"{self.name}, active")
        if not active:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", "true" if active else "false")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        box = QVBoxLayout(self)
        box.setContentsMargins(2, 2, 2, 2)
        box.setSpacing(0)
        self.preview = PreviewPane(profile)
        box.addWidget(self.preview, 1)
        if active:
            self.badge = QLabel("ACTIVE", self.preview, objectName="badge")
            self.badge.ensurePolished()
            self.badge.adjustSize()
            self.badge.move(14, 12)

        line = QFrame(objectName="divider")
        line.setFixedHeight(1)
        box.addWidget(line)

        footer = QHBoxLayout()
        footer.setContentsMargins(18, 12, 14, 12)
        footer.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name_label = QLabel(objectName="cardName")
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setText(self.name)
        self.name_label.setToolTip(self.name)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status = QLabel(objectName="muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.name_label)
        text.addWidget(self.status)
        footer.addLayout(text, 1)

        self.default_box = DefaultCheck("Default", checked=is_default)
        self.default_box.setAccessibleName(f"Use {self.name} as the default profile")
        self.default_box.setToolTip("The default profile returns at login and after games,\n"
                                    "and is used for games without a profile.")
        self.default_box.clicked.connect(self._make_default)
        footer.addWidget(self.default_box)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setToolTip(f"Apply and edit {self.name}" if not active
                                 else f"Edit {self.name}")
        self.edit_btn.clicked.connect(lambda: self.window_.edit_profile(self.name))
        footer.addWidget(self.edit_btn)
        self.more = QToolButton(objectName="iconButton", toolTip=f"Actions for {self.name}")
        self.more.setAccessibleName(f"Actions for {self.name}")
        self.more.setIcon(theme.icon("more", theme.TEXT, 18))
        self.more.setIconSize(QSize(18, 18))
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more)
        menu.addAction("Rename…", lambda: self.window_.rename_profile(self.name))
        menu.addAction("Delete…", lambda: self.window_.delete_profile(self.name))
        self.more.setMenu(menu)
        footer.addWidget(self.more)
        box.addLayout(footer)
        self.sync(profile, False)

    def sync(self, profile, dirty):
        self.profile = profile
        self.preview.set_profile(profile)
        if self.active:
            session = self.window_.game_session
            if dirty:
                text = "Unsaved changes"
            elif session and session[0] == self.name:
                text = f"Until {session[1] or 'the game'} exits"
            elif self.is_default:
                text = "Restored at login"
            else:
                text = "Applied for now"
            self.status.setText(text)
            self.status.setToolTip(text)
            self.status.setObjectName("unsaved" if dirty else "muted")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        else:
            lights = sum(1 for i in layout.build_items() if i.enabled)
            self.status.setText(f"{lights} lights · {profile.brightness}% brightness")

    def _make_default(self):
        self.default_box.setChecked(True)       # one profile is always the default
        if not self.is_default:
            self.window_.set_default_profile(self.name)

    def apply(self):
        if not self.active:
            self.window_.switch_profile(self.name)

    def mousePressEvent(self, e):
        self._pressed = e.button() == Qt.MouseButton.LeftButton

    def mouseReleaseEvent(self, e):
        clicked = (self._pressed and e.button() == Qt.MouseButton.LeftButton
                   and self.rect().contains(e.position().toPoint()))
        self._pressed = False
        if clicked:
            self.apply()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.window_.edit_profile(self.name)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.apply()
        else:
            super().keyPressEvent(e)


class NewProfileCard(QFrame):
    def __init__(self, on_click):
        super().__init__(objectName="newCard")
        self.on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        box = QVBoxLayout(self)
        box.addStretch()
        mark = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(theme.icon("plus", theme.TEXT_SECONDARY, 26).pixmap(26, 26))
        box.addWidget(mark)
        title = QLabel("New profile", alignment=Qt.AlignmentFlag.AlignCenter)
        title.setObjectName("cardName")
        box.addWidget(title)
        hint = QLabel("Starts from your current colors", objectName="muted",
                      alignment=Qt.AlignmentFlag.AlignCenter)
        box.addWidget(hint)
        box.addStretch()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.on_click()


class LibraryPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.cards = {}
        self._widgets = []
        self._stale = True
        self._columns = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 26, 26, 18)
        outer.setSpacing(20)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(7)
        mark = QLabel()
        mark.setPixmap(theme.icon("mark", theme.ACCENT, 20).pixmap(20, 20))
        brand.addWidget(mark)
        brand.addWidget(QLabel("Lighting Studio", objectName="appName"))
        brand.addStretch()
        titles.addLayout(brand)
        titles.addWidget(QLabel("Your profiles", objectName="pageTitle"))
        subtitle = QLabel("Click a profile to apply it. Your default returns at login and after games.", objectName="muted")
        titles.addWidget(subtitle)
        head.addLayout(titles)
        head.addStretch()
        self.games_btn = QPushButton(theme.icon("game", theme.TEXT, 18), "Games")
        self.games_btn.setToolTip("Choose profiles for games and game types")
        self.games_btn.clicked.connect(lambda: self.window_.show_games())
        head.addWidget(self.games_btn)
        head.addSpacing(10)
        self.search = QLineEdit(objectName="search", placeholderText="Search profiles")
        self.search.setClearButtonEnabled(True)
        self.search.setFocusPolicy(Qt.FocusPolicy.ClickFocus)   # no caret on opening
        self.search.setFixedWidth(280)
        self.search.addAction(theme.icon("search", theme.TEXT_SECONDARY, 16),
                              QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda _: self._reflow())
        head.addWidget(self.search)
        outer.addLayout(head)

        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 2, 10, 10)
        self.grid.setSpacing(GRID_GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)
        self.empty = QLabel("No profiles match your search.", self.body, objectName="muted")
        self.empty.hide()
        self.new_card = NewProfileCard(lambda: self.window_.save_as())
        self.new_card.setParent(self.body)

    # ---- data ----
    def invalidate(self):
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def refresh(self):
        self._stale = False
        for card in self.cards.values():
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = {}
        current = self.window_.profile
        default = self.window_.store.active_name()
        for _, name in self.window_.store.list():
            active = name == current.name
            try:
                profile = current if active else self.window_.store.load(name)
            except (OSError, ValueError, ProfileError) as e:   # a file can vanish between calls
                print(f"alienfx-studio: skipping {name}: {e}", file=sys.stderr)
                continue
            card = ProfileCard(self.window_, profile, active, name == default)
            card.setParent(self.body)
            self.cards[name] = card
        if current.name in self.cards:
            self.cards[current.name].sync(current, self.window_.dirty)
        self._columns = 0
        self._reflow()

    def sync_active(self):
        """Show in-memory edits on the active card without rebuilding the grid."""
        if not self.isVisible():
            self._stale = True
            return
        current = self.window_.profile
        card = self.cards.get(current.name)
        if card:
            card.sync(current, self.window_.dirty)

    # ---- layout ----
    def _column_count(self):
        width = self.scroll.viewport().width() - 10
        return max(1, min(4, (width + GRID_GAP) // (CARD_MIN_WIDTH + GRID_GAP)))

    def _reflow(self):
        columns = self._column_count()
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        query = self.search.text().strip().lower()
        shown = [c for n, c in self.cards.items() if query in n.lower()]
        for card in self.cards.values():
            card.setVisible(card in shown)
        cells = list(shown)
        self.new_card.setVisible(not query)
        if not query:
            cells.append(self.new_card)
        for index, widget in enumerate(cells):
            self.grid.addWidget(widget, index // columns, index % columns)
        for column in range(5):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
        self.empty.setVisible(bool(query) and not shown)
        if self.empty.isVisible():
            self.grid.addWidget(self.empty, 0, 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._column_count() != self._columns:
            self._reflow()

    def showEvent(self, e):
        super().showEvent(e)
        if self._stale:
            self.refresh()
        else:
            self._reflow()
