"""Keyboard renderer: blank graphite keycaps whose legends glow in their LED color."""
import math
from collections import OrderedDict
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QTransform)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from . import layout, theme

DECK = 0.11
_GAP = 0.07
_RADIUS = 0.12
_LEVELS = 32             # brightness steps, which bounds the number of cached glows
_CACHE_BYTES = 48 * 1024 * 1024
_REFERENCE_PX = 100.0

# kind: (font size in key units, top of capitals as a fraction of key height)
_TEXT_STYLE = {"alpha": (.235, .19), "symbol": (.2, .17), "function": (.112, .2),
               "modifier": (.125, None), "top_modifier": (.115, .18)}
_ICON_STYLE = {"icon": (.29, .22), "nav": (.2, .15)}


def _rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


@lru_cache(maxsize=4096)
def led_state(value, brightness):
    """Return (normalized rgb, quantized strength) for a stored color and brightness.

    Stored colors are never changed. Strength 0 means the LED is effectively off.
    """
    r, g, b = _rgb(value)
    peak = max(r, g, b)
    if not peak or brightness <= 0:
        return (0, 0, 0), 0
    norm = tuple(round(c * 255 / peak) for c in (r, g, b))
    return norm, max(1, round(peak / 255 * brightness / 100 * _LEVELS))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


_UNLIT = _rgb(theme.LEGEND_UNLIT)
_WHITE = (255, 255, 255)


def legend_color(norm, level):
    """The LED color mixed about 68% with white, faded toward the unlit legend."""
    if not level:
        return _UNLIT
    return _mix(_UNLIT, _mix(norm, _WHITE, .32), level / _LEVELS)


class _Cache:
    """A small LRU of pixmaps, bounded by memory rather than entry count."""

    def __init__(self, limit):
        self.limit, self.size, self.items = limit, 0, OrderedDict()

    def get(self, key):
        value = self.items.get(key)
        if value is not None:
            self.items.move_to_end(key)
        return value

    def put(self, key, value, cost):
        self.items[key] = (value, cost)
        self.size += cost
        while self.size > self.limit and len(self.items) > 1:
            _, (_, old) = self.items.popitem(last=False)
            self.size -= old


_tiles = _Cache(_CACHE_BYTES)
_masks = _Cache(_CACHE_BYTES // 2)
_paths = {}


def _text_path(family, text):
    key = (family, text)
    if key not in _paths:
        path = QPainterPath()
        path.addText(0, 0, theme.font(family, _REFERENCE_PX), text)
        _paths[key] = path
    return _paths[key]


def legend_path(item, width, height, unit):
    """Build the legend as a filled path, relative to the key's top-left corner."""
    if item.kind == "top_icon":
        # Share the FN and ALT top line, measured from the icon's visible edge.
        icon = theme.icon_path(item.legend)
        bounds = icon.boundingRect()
        scale = unit * .25 / 24
        x = (width - bounds.width() * scale) / 2 - bounds.left() * scale
        y = height * _TEXT_STYLE["top_modifier"][1] - bounds.top() * scale
        return QTransform().translate(x, y).scale(scale, scale).map(icon)
    if item.kind in _ICON_STYLE:
        scale, top = _ICON_STYLE[item.kind]
        size = unit * scale
        transform = QTransform().translate((width - size) / 2, height * top).scale(size / 24,
                                                                                   size / 24)
        return transform.map(theme.icon_path(item.legend))
    size, top = _TEXT_STYLE[item.kind]
    scale = unit * size / _REFERENCE_PX
    base = _text_path(theme.KEYCAP, item.legend)
    bounds = base.boundingRect()
    cap = _text_path(theme.KEYCAP, "H").boundingRect().height() * scale
    x = width * .17 if item.kind == "function" else (width - bounds.width() * scale) / 2
    y = (height + cap) / 2 if top is None else height * top + cap
    return QTransform().translate(x - bounds.left() * scale, y).scale(scale, scale).map(base)


def _blurred(image, radius):
    """A fast blur: shrink, then smoothly enlarge."""
    factor = max(1.0, radius)
    w, h = image.width(), image.height()
    small = image.scaled(max(1, round(w / factor)), max(1, round(h / factor)),
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def _glow_mask(key, path, tile, ratio, radius, outside_only):
    cached = _masks.get(key)
    if cached:
        return cached[0]
    image = QImage(max(1, round(tile.width() * ratio)), max(1, round(tile.height() * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(ratio, ratio)
    p.translate(-tile.left(), -tile.top())
    p.fillPath(path, QColor(255, 255, 255))
    p.end()
    wide, tight = _blurred(image, radius * ratio * 1.5), _blurred(image, radius * ratio * .55)
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.drawImage(0, 0, wide)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    p.drawImage(0, 0, tight)
    if outside_only:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.scale(ratio, ratio)
        p.translate(-tile.left(), -tile.top())
        p.fillPath(path, QColor(0, 0, 0))
    p.end()
    _masks.put(key, out, out.sizeInBytes())
    return out


def _tile(key, path, ratio, radius, glow, core, opacity, outside_only=False):
    """Return (pixmap, offset) for a path with a colored glow and optional solid core."""
    full = (key, glow, core, opacity, round(ratio * 100))
    cached = _tiles.get(full)
    if cached:
        return cached[0]
    pad = radius * 2 + 1
    tile = path.boundingRect().adjusted(-pad, -pad, pad, pad).toAlignedRect()
    if opacity > 0:
        mask = _glow_mask((key, round(ratio * 100), round(radius * 8)), path, tile, ratio, radius,
                          outside_only)
        image = mask.copy()
    else:
        image = QImage(max(1, round(tile.width() * ratio)), max(1, round(tile.height() * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if opacity > 0:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(image.rect(), QColor(*glow))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.fillRect(image.rect(), QColor(0, 0, 0, round(255 * opacity)))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    if core is not None:
        p.scale(ratio, ratio)
        p.translate(-tile.left(), -tile.top())
        p.fillPath(path, QColor(*core))
    p.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    value = (pixmap, QPointF(tile.topLeft()))
    _tiles.put(full, value, image.sizeInBytes())
    return value


class KeyboardView(QWidget):
    """Draws keys and ELC zones; handles click, Ctrl-click and rubber-band select."""
    selectionChanged = pyqtSignal()
    paintStarted = pyqtSignal()
    paintItem = pyqtSignal(str)
    paintFinished = pyqtSignal()

    def __init__(self, color_of, brightness_of=lambda: 100, read_only=False):
        super().__init__()
        self.read_only = read_only
        self.items = [i for i in layout.build_items()
                      if not read_only or i.kind != "zone"]
        self._lit = [i for i in self.items if i.enabled and (i.legend or i.kind == "zone")]
        self.color_of = color_of
        self.brightness_of = brightness_of
        self.selected = set()
        self._origin = None
        self._band = None
        self._base = set()
        self.paint_mode = False
        self._stroke = False
        self._last = None
        self._painted = set()
        self._hover = None
        self._static = None
        self._static_key = None
        self._paths = {}
        if read_only:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setMinimumSize(240, 86)
        else:
            self.setMinimumSize(640, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- geometry ----
    def _content_height(self):
        return layout.KEYBOARD_HEIGHT + DECK if self.read_only else layout.HEIGHT

    def _unit(self):
        margin = 2 if self.read_only else 6
        return max(1.0, min((self.width() - margin * 2) / (layout.WIDTH + DECK * 2),
                            (self.height() - margin * 2) / (self._content_height() + DECK)))

    def _origin_point(self):
        u = self._unit()
        total = (self._content_height() + DECK) * u
        return QPointF((self.width() - layout.WIDTH * u) / 2,
                       (self.height() - total) / 2 + DECK * u)

    def _snap(self, value):
        ratio = self.devicePixelRatioF()
        return round(value * ratio) / ratio

    def _rect(self, item):
        u = self._unit()
        o = self._origin_point()
        gap = u * _GAP
        left = self._snap(o.x() + item.x * u + gap / 2)
        top = self._snap(o.y() + item.y * u + gap / 2)
        right = self._snap(o.x() + (item.x + item.w) * u - gap / 2)
        bottom = self._snap(o.y() + (item.y + item.h) * u - gap / 2)
        return QRectF(left, top, right - left, bottom - top)

    def _bar(self, item):
        rect = self._rect(item)
        rect.setHeight(max(6.0, self._snap(self._unit() * .3)))
        return rect

    def _at(self, pos):
        return next((i for i in self.items if i.enabled and self._rect(i).contains(pos)), None)

    # ---- painting ----
    def _build_static(self):
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHints(QPainter.RenderHint.Antialiasing
                         | QPainter.RenderHint.TextAntialiasing)
        u = self._unit()
        o = self._origin_point()
        deck = QRectF(o.x() - DECK * u, o.y() - DECK * u, (layout.WIDTH + DECK * 2) * u,
                      (layout.KEYBOARD_HEIGHT + DECK * 2) * u)
        gradient = QLinearGradient(deck.topLeft(), deck.bottomLeft())
        gradient.setColorAt(0, QColor(theme.DECK_TOP))
        gradient.setColorAt(1, QColor(theme.DECK_BOTTOM))
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(gradient)
        p.drawRoundedRect(deck, u * .15, u * .15)
        for item in self.items:
            if item.kind == "zone":
                bar = self._bar(item)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.ZONE_OFF))
                p.drawRoundedRect(bar, 3, 3)
            else:
                self._draw_keycap(p, item, u)
        if not self.read_only:
            p.setFont(theme.font(theme.SANS, max(10, u * .17)))
            p.setPen(QColor(theme.TEXT_SECONDARY))
            for title, x in layout.SECTIONS:
                p.drawText(QPointF(o.x() + x * u + 2, o.y() + (layout.SECTION_Y + .22) * u), title)
        p.end()
        return pixmap

    def _draw_keycap(self, p, item, u):
        r = self._rect(item)
        radius = u * _RADIUS
        gradient = QLinearGradient(r.topLeft(), r.bottomLeft())
        gradient.setColorAt(0, QColor(theme.KEY_TOP))
        gradient.setColorAt(1, QColor(theme.KEY_BOTTOM))
        p.setPen(QPen(QColor(theme.KEY_EDGE), max(1.0, u * .025)))
        p.setBrush(gradient)
        p.drawRoundedRect(r, radius, radius)
        if u >= 34:
            p.setPen(QPen(QColor(255, 255, 255, 16), 1))
            p.drawLine(QPointF(r.left() + radius, r.top() + 2),
                       QPointF(r.right() - radius, r.top() + 2))
        if not item.enabled and item.legend:
            path, _ = self._legend(item, r, u)
            p.fillPath(path.translated(r.topLeft()), QColor(theme.LEGEND_UNLIT))
        if self.read_only:
            return
        if item.homing:
            p.setPen(QPen(QColor(0, 0, 0, 150), max(1.0, u * .02)))
            p.drawLine(QPointF(r.center().x() - r.width() * .11, r.bottom() - r.height() * .09),
                       QPointF(r.center().x() + r.width() * .11, r.bottom() - r.height() * .09))
        if item.secondary:
            self._draw_secondary(p, item, r, u)

    def _draw_secondary(self, p, item, r, u):
        if item.kind == "function":
            size, flags = u * .105, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
            area = r.adjusted(0, 0, -r.width() * .13, -r.height() * .13)
            color = "#8D9096"
        elif item.kind == "nav":
            size, flags = u * .075, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
            area = r.adjusted(0, 0, 0, -r.height() * .15)
            color = theme.LEGEND_SECONDARY
        else:
            size, flags = u * .17, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom
            area = r.adjusted(0, 0, 0, -r.height() * .12)
            color = theme.LEGEND_SECONDARY
        if size < 5:
            return
        p.setFont(theme.font(theme.SANS, size, QFont.Weight.Medium))
        p.setPen(QColor(color))
        p.drawText(area, flags, item.secondary)

    def _legend(self, item, rect, unit):
        key = (item.legend, item.kind, round(rect.width() * 4), round(rect.height() * 4),
               round(unit * 8))
        if key not in self._paths:
            self._paths[key] = legend_path(item, rect.width(), rect.height(), unit)
        return self._paths[key], key

    def paintEvent(self, _):
        ratio = self.devicePixelRatioF()
        static_key = (self.width(), self.height(), ratio)
        if self._static_key != static_key:
            self._paths.clear()
            self._static = self._build_static()
            self._static_key = static_key
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._static)
        u = self._unit()
        brightness = self.brightness_of()
        glow_radius = max(1.5, u * .075)

        states = {}
        for item in self._lit:
            states[item.id] = led_state(self.color_of(item.id), brightness)

        for item in self.items:
            if item.id in self.selected and item.kind != "zone":
                self._draw_selection_glow(p, item, states[item.id], u, ratio)
        for item in self._lit:
            norm, level = states[item.id]
            if item.kind == "zone":
                self._draw_zone(p, item, norm, level, u, ratio)
            else:
                rect = self._rect(item)
                path, key = self._legend(item, rect, u)
                core = legend_color(norm, level)
                pixmap, offset = _tile(("legend", key), path, ratio, glow_radius, norm, core,
                                       min(1.0, level / _LEVELS) * .85)
                p.drawPixmap(rect.topLeft() + offset, pixmap)
        if self.selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            for item in self.items:
                if item.id in self.selected:
                    rect = self._bar(item) if item.kind == "zone" else self._rect(item)
                    radius = 4 if item.kind == "zone" else u * _RADIUS + 1
                    p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius, radius)
        if self.paint_mode and self._hover is not None:
            zone = self._hover.kind == "zone"
            rect = self._bar(self._hover) if zone else self._rect(self._hover)
            radius = 4 if zone else u * _RADIUS + 1
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 170), 2))
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), radius, radius)
        if self._band:
            p.setPen(QPen(QColor(255, 255, 255, 170), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(self._band, 3, 3)
        p.end()

    def _draw_selection_glow(self, p, item, state, u, ratio):
        norm, level = state
        color = norm if level else _WHITE
        rect = self._rect(item)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, rect.width(), rect.height()), u * _RADIUS, u * _RADIUS)
        key = ("selection", round(rect.width() * 4), round(rect.height() * 4), round(u * 8))
        pixmap, offset = _tile(key, path, ratio, max(2.0, u * .1), color, None, .8, True)
        p.drawPixmap(rect.topLeft() + offset, pixmap)

    def _draw_zone(self, p, item, norm, level, u, ratio):
        if not level:
            return
        rect = self._bar(item)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, rect.width(), rect.height()), 3, 3)
        off = _rgb(theme.ZONE_OFF)
        core = _mix(off, norm, level / _LEVELS)
        key = ("zone", round(rect.width() * 4), round(rect.height() * 4))
        pixmap, offset = _tile(key, path, ratio, max(2.0, u * .09), norm, core,
                               level / _LEVELS * .6)
        p.drawPixmap(rect.topLeft() + offset, pixmap)

    # ---- paint mode ----
    def set_paint_mode(self, on):
        """In paint mode, clicks and drags apply a color instead of selecting."""
        if on == self.paint_mode:
            return
        self._end_stroke()
        self.paint_mode = on
        self._origin = self._band = self._hover = None
        self.setMouseTracking(on)
        if on:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.select(set())
        else:
            self.unsetCursor()
        self.update()

    def _set_hover(self, item):
        if item is not self._hover:
            self._hover = item
            self.update()

    def _paint_at(self, pos):
        item = self._at(pos)
        if item and item.id not in self._painted:
            self._painted.add(item.id)
            self.paintItem.emit(item.id)

    def _paint_line(self, start, end):
        """Sample along the drag so a fast mouse cannot skip keys."""
        delta = end - start
        steps = max(1, math.ceil(math.hypot(delta.x(), delta.y()) / max(2.0, self._unit() * .25)))
        for n in range(1, steps + 1):
            self._paint_at(start + delta * (n / steps))

    def _end_stroke(self):
        if self._stroke:
            self._stroke = False
            self._last = None
            self._painted.clear()
            self.paintFinished.emit()

    def leaveEvent(self, e):
        self._set_hover(None)
        super().leaveEvent(e)

    def hideEvent(self, e):
        self._end_stroke()
        self._set_hover(None)
        super().hideEvent(e)

    # ---- input ----
    def mousePressEvent(self, e):
        if self.read_only or e.button() != Qt.MouseButton.LeftButton:
            return
        if self.paint_mode:
            self._stroke, self._last = True, e.position()
            self._painted.clear()
            self.paintStarted.emit()
            self._paint_at(e.position())
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
        if self.paint_mode:
            self._set_hover(self._at(e.position()))
            if self._stroke:
                self._paint_line(self._last, e.position())
                self._last = e.position()
            return
        if self._origin is None:
            return
        if (e.position() - self._origin).manhattanLength() < 5:
            return
        self._band = QRectF(self._origin, e.position()).normalized()
        self.selected = self._base | {i.id for i in self.items
                                      if i.enabled and self._band.intersects(self._rect(i))}
        self.selectionChanged.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self.paint_mode:
            if e.button() == Qt.MouseButton.LeftButton:
                self._end_stroke()
            return
        self._origin = None
        self._band = None
        self.update()

    def select(self, ids):
        self.selected = set(ids) & self.all_ids()
        self.selectionChanged.emit()
        self.update()

    def all_ids(self):
        return {i.id for i in self.items if i.enabled}

    def sizeHint(self):
        return QSize(1100, 480) if not self.read_only else QSize(420, 150)
