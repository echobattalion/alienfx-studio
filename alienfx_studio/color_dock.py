"""The bottom color dock: swatch, hex, hue and saturation, recents, and actions."""
import colorsys
import re

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractButton, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme

_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")
RECENT_SLOTS = 10


def parse_hex(text):
    """Return a QColor for '#RRGGBB' or 'RRGGBB', or None if the text is invalid."""
    text = text.strip()
    if not _HEX.match(text):
        return None
    return QColor("#" + text.lstrip("#"))


class GradientSlider(QWidget):
    """A horizontal 0 to 1 slider drawn over a color gradient."""
    valueChanged = pyqtSignal(float)
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, stops, accessible_name):
        super().__init__()
        self.stops = stops
        self.value = 0.0
        self.thumb = QColor("#FFFFFF")
        self._dragging = False
        self.setAccessibleName(accessible_name)
        self.setFixedHeight(22)
        self.setMinimumWidth(120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, value):
        self.value = max(0.0, min(1.0, value))
        self.update()

    def set_stops(self, stops, thumb):
        self.stops, self.thumb = stops, thumb
        self.update()

    def _from_x(self, x):
        return max(0.0, min(1.0, (x - 9) / max(1, self.width() - 18)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(2, self.height() / 2 - 5, self.width() - 4, 10)
        gradient = QLinearGradient(track.topLeft(), track.topRight())
        for at, color in self.stops:
            gradient.setColorAt(at, QColor(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawRoundedRect(track, 5, 5)
        x = 9 + self.value * (self.width() - 18)
        center = QPointF(x, self.height() / 2)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(center + QPointF(0, 1), 10, 10)
        p.setBrush(self.thumb)
        p.setPen(QPen(QColor("#FFFFFF"), 3))
        p.drawEllipse(center, 8, 8)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.drawEllipse(center, 11, 11)

    def _move(self, x):
        value = self._from_x(x)
        if value != self.value:
            self.value = value
            self.update()
            self.valueChanged.emit(value)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.pressed.emit()
            self._move(e.position().x())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._move(e.position().x())

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.released.emit()

    def keyPressEvent(self, e):
        step = {Qt.Key.Key_Left: -.01, Qt.Key.Key_Down: -.01,
                Qt.Key.Key_Right: .01, Qt.Key.Key_Up: .01}.get(e.key())
        if step is None:
            super().keyPressEvent(e)
            return
        value = max(0.0, min(1.0, self.value + step))
        if value != self.value:
            self.value = value
            self.update()
            self.valueChanged.emit(value)


class SaturationValueSquare(QWidget):
    """A saturation (x) and value (y) picking square."""
    changed = pyqtSignal(float, float)
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hue, self.sat, self.val = 0.0, 1.0, 1.0
        self._down = False
        self.setFixedSize(248, 168)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_hsv(self, h, s, v):
        self.hue, self.sat, self.val = h, s, v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        horizontal = QLinearGradient(rect.topLeft(), rect.topRight())
        horizontal.setColorAt(0, QColor("#FFFFFF"))
        horizontal.setColorAt(1, QColor.fromHsvF(min(self.hue, .9999), 1, 1))
        p.setBrush(horizontal)
        p.drawRoundedRect(rect, 8, 8)
        vertical = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        vertical.setColorAt(0, QColor(0, 0, 0, 0))
        vertical.setColorAt(1, QColor(0, 0, 0, 255))
        p.setBrush(vertical)
        p.drawRoundedRect(rect, 8, 8)
        center = QPointF(self.sat * rect.width(), (1 - self.val) * rect.height())
        p.setBrush(QColor.fromHsvF(min(self.hue, .9999), self.sat, self.val))
        p.setPen(QPen(QColor("#FFFFFF"), 3))
        p.drawEllipse(center, 8, 8)

    def _emit(self, pos):
        s = max(0.0, min(1.0, pos.x() / self.width()))
        v = 1 - max(0.0, min(1.0, pos.y() / self.height()))
        self.sat, self.val = s, v
        self.update()
        self.changed.emit(s, v)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._down = True
            self.pressed.emit()
            self._emit(e.position())

    def mouseMoveEvent(self, e):
        if self._down:
            self._emit(e.position())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.finish()

    def finish(self):
        """End a drag, including one cut short by the popover closing."""
        if self._down:
            self._down = False
            self.released.emit()


class Popover(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.square = SaturationValueSquare()
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 10, 10, 10)
        box.addWidget(self.square)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.square.finish()


class Swatch(QAbstractButton):
    """A rounded color button, used for the large swatch and recent colors."""

    def __init__(self, size, radius, glow=False):
        super().__init__()
        self.color = None
        self.radius = radius
        self.glow = glow
        self.marked = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color, marked=False):
        self.color, self.marked = color, marked
        self.setEnabled(color is not None)
        self.setToolTip(color.name().upper() if color is not None else "")
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = 7 if self.glow else 3
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        if self.color is None:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.setBrush(QColor(255, 255, 255, 6))
            p.drawRoundedRect(rect, self.radius, self.radius)
            return
        if self.glow:
            p.setPen(Qt.PenStyle.NoPen)
            for grow, alpha in ((6, 16), (4, 24), (2, 36)):
                halo = QColor(self.color)
                halo.setAlpha(alpha)
                p.setBrush(halo)
                p.drawRoundedRect(rect.adjusted(-grow, -grow, grow, grow),
                                  self.radius + grow, self.radius + grow)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(self.color)
        p.drawRoundedRect(rect, self.radius, self.radius)
        if self.marked:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), self.radius + 2, self.radius + 2)


def _divider():
    line = QFrame(objectName="divider")
    line.setFixedSize(1, 54)
    return line


class ColorDock(QFrame):
    """The color picker. Exposes the same core API as QColorDialog."""
    currentColorChanged = pyqtSignal(QColor)
    interactionStarted = pyqtSignal()
    interactionFinished = pyqtSignal()

    HUE_STOPS = [(i / 6, c) for i, c in enumerate(
        ("#FF0000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF", "#FF00FF", "#FF0000"))]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(92)
        self.setMaximumWidth(1320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._color = QColor("#FF0000")
        self._h, self._s, self._v = 0.0, 1.0, 1.0
        self._recent = []

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 16, 8)
        row.setSpacing(14)

        self.swatch = Swatch(70, 14, glow=True)
        self.swatch.setToolTip("Open the shade picker")
        self.swatch.setAccessibleName("Open the shade picker")
        self.swatch.clicked.connect(self._open_popover)
        row.addWidget(self.swatch)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addStretch()
        self.selection_label = QLabel("Select keys to color", objectName="muted")
        self.selection_label.setFixedWidth(136)
        info.addWidget(self.selection_label)
        self.hex = QLineEdit(objectName="hex", maxLength=7)
        self.hex.setFixedWidth(136)
        self.hex.setAccessibleName("Hex color")
        self.hex.textEdited.connect(self._hex_edited)
        self.hex.editingFinished.connect(self._refresh_hex)
        info.addWidget(self.hex)
        info.addStretch()
        row.addLayout(info)
        row.addWidget(_divider())

        sliders = QVBoxLayout()
        sliders.setSpacing(6)
        sliders.addStretch()
        self.hue = GradientSlider(self.HUE_STOPS, "Hue")
        self.hue.valueChanged.connect(self._hue_moved)
        self.saturation = GradientSlider([(0, "#FFFFFF"), (1, "#FF0000")], "Saturation")
        self.saturation.valueChanged.connect(self._saturation_moved)
        for slider in (self.hue, self.saturation):
            slider.pressed.connect(self.interactionStarted)
            slider.released.connect(self.interactionFinished)
            sliders.addWidget(slider)
        sliders.addStretch()
        row.addLayout(sliders, 1)
        row.addWidget(_divider())

        grid = QGridLayout()
        grid.setSpacing(3)
        self.recent_swatches = []
        for i in range(RECENT_SLOTS):
            swatch = Swatch(30, 6)
            swatch.setEnabled(False)
            swatch.clicked.connect(lambda _=False, n=i: self._recent_clicked(n))
            grid.addWidget(swatch, i // 5, i % 5)
            self.recent_swatches.append(swatch)
        row.addLayout(grid)
        row.addWidget(_divider())

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.unsaved = QLabel("Unsaved", objectName="unsaved")
        self.unsaved.setVisible(False)
        actions.addWidget(self.unsaved)
        self.paint_btn = self._icon_button("brush", "Paint mode (B)")
        self.paint_btn.setIcon(theme.icon("brush", theme.TEXT, 18, "#111111"))
        self.paint_btn.setCheckable(True)
        self.fill_btn = self._icon_button("fill", "Fill selection with this color")
        self.undo_btn = self._icon_button("undo", "Undo (Ctrl+Z)")
        self.more_btn = self._icon_button("more", "More actions")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        for button in (self.paint_btn, self.fill_btn, self.undo_btn, self.more_btn):
            actions.addWidget(button)
        self.save_btn = QPushButton(theme.icon("check", "#111111", 16), "Save", objectName="primary")
        self.save_btn.setToolTip("Save (Ctrl+S)")
        actions.addWidget(self.save_btn)
        row.addLayout(actions)

        self.popover = Popover(self)
        self.popover.square.changed.connect(self._square_moved)
        self.popover.square.pressed.connect(self.interactionStarted)
        self.popover.square.released.connect(self.interactionFinished)
        self._refresh()

    @staticmethod
    def _icon_button(name, tip):
        button = QToolButton(objectName="iconButton", toolTip=tip)
        button.setAccessibleName(tip)
        button.setIcon(theme.icon(name, theme.TEXT, 18))
        button.setIconSize(QSize(18, 18))
        return button

    # ---- QColorDialog-compatible API ----
    def currentColor(self):
        return QColor(self._color)

    def setCurrentColor(self, color):
        color = QColor(color)
        if not color.isValid():
            return
        rgb = color.getRgb()[:3]
        if rgb == self._color.getRgb()[:3]:
            self._refresh()
            return
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        if v > 0:
            self._s = s
            if s > 0:
                self._h = h
        self._v = v
        self._color = QColor(*rgb)
        self._refresh()
        self.currentColorChanged.emit(self.currentColor())

    # ---- dock state ----
    def set_selection_label(self, text):
        self.selection_label.setText(text)
        self.selection_label.setToolTip(text)

    def set_dirty(self, dirty):
        self.unsaved.setVisible(dirty)

    def set_recent(self, colors):
        self._recent = list(colors)[:RECENT_SLOTS]
        self._refresh_recent()

    def _refresh_recent(self):
        current = self._color.name().upper()
        for i, swatch in enumerate(self.recent_swatches):
            value = self._recent[i] if i < len(self._recent) else None
            swatch.set_color(QColor(value) if value else None, value == current)

    def _recent_clicked(self, index):
        if index < len(self._recent):
            self.setCurrentColor(QColor(self._recent[index]))

    # ---- input ----
    def _set_hsv(self, h, s, v):
        self._h, self._s, self._v = h, s, v
        rgb = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
        changed = rgb != self._color.getRgb()[:3]
        self._color = QColor(*rgb)
        self._refresh()
        if changed:
            self.currentColorChanged.emit(self.currentColor())

    def _hue_moved(self, value):
        self._set_hsv(value, self._s, self._v)

    def _saturation_moved(self, value):
        self._set_hsv(self._h, value, self._v)

    def _square_moved(self, s, v):
        self._set_hsv(self._h, s, v)

    def _hex_edited(self, text):
        color = parse_hex(text)
        if color is not None:
            self.setCurrentColor(color)

    def _refresh_hex(self):
        self.hex.setText(self._color.name().upper())

    def _open_popover(self):
        self.popover.square.set_hsv(self._h, self._s, self._v)
        self.popover.adjustSize()
        anchor = self.swatch.mapToGlobal(QPoint(0, 0))
        self.popover.move(anchor.x(), anchor.y() - self.popover.height() - 10)
        self.popover.show()

    def _refresh(self):
        self.swatch.set_color(self._color)
        self.swatch.setToolTip("Open the shade picker")
        parsed = parse_hex(self.hex.text())
        if not self.hex.hasFocus() or parsed is None or parsed != self._color:
            if not (self.hex.hasFocus() and parsed is None):
                self._refresh_hex()
        self.hue.set_value(self._h)
        self.hue.thumb = QColor.fromHsvF(min(self._h, .9999), 1, 1)
        self.saturation.set_value(self._s)
        self.saturation.set_stops([(0, "#FFFFFF"), (1, self.hue.thumb.name())], self._color)
        if self.popover.isVisible():
            self.popover.square.set_hsv(self._h, self._s, self._v)
        self._refresh_recent()
