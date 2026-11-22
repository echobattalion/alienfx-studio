"""Obsidian theme: the single source of colors, fonts, icons and the stylesheet."""
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter,
                         QPainterPath, QPainterPathStroker, QPalette, QPixmap, QTransform)

WINDOW = "#08080B"
WINDOW_MID = "#0D0A12"
GLOW = "#2A0B35"
SURFACE = "#1A1720"
SURFACE_HOVER = "#25212C"
BORDER = "rgba(255, 255, 255, 26)"
TEXT = "#F2EFF6"
TEXT_SECONDARY = "#938D9C"
ACCENT = "#FF4DFF"
DANGER = "#FF5A67"
OK = "#46E08A"
DECK_TOP = "#5A5D62"
DECK_BOTTOM = "#44474B"
KEY_TOP = "#37393D"
KEY_BOTTOM = "#2B2D31"
KEY_EDGE = "#0B0C0E"
LEGEND_UNLIT = "#62656B"
LEGEND_SECONDARY = "#9B9EA4"
ZONE_OFF = "#26252B"

SANS = "Geist"
MONO = "Geist Mono"
KEYCAP = "Michroma"

_FONT_FILES = {SANS: ("Geist-Regular.ttf", "Geist-Medium.ttf", "Geist-SemiBold.ttf"),
               MONO: ("GeistMono-Regular.ttf", "GeistMono-Medium.ttf"),
               KEYCAP: ("Michroma.ttf",)}
_loaded = {}


def load_fonts():
    """Load bundled fonts once. A missing font falls back to the system sans-serif."""
    if _loaded:
        return
    root = Path(__file__).parent / "assets" / "fonts"
    for family, filenames in _FONT_FILES.items():
        _loaded[family] = None
        for filename in filenames:
            font_id = QFontDatabase.addApplicationFont(str(root / filename))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families and _loaded[family] is None:
                _loaded[family] = families[0]


def font(family=SANS, pixel_size=13, weight=QFont.Weight.Normal):
    load_fonts()
    resolved = _loaded.get(family)
    f = QFont(resolved) if resolved else QFont()
    if not resolved:
        f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(max(1, round(pixel_size)))
    f.setWeight(weight)
    return f


def _stroked(path, width=2.2):
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    out = stroker.createStroke(path)
    out.setFillRule(Qt.FillRule.WindingFill)
    return out


def _poly(path, *points, close=True):
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()


def _speaker(path):
    _poly(path, (3, 9), (3, 15), (7, 15), (12, 19), (12, 5), (7, 9))


def _build_icon(name):
    p = QPainterPath()
    if name == "win":
        _poly(p, (3, 5), (11, 3.8), (11, 11), (3, 11))
        _poly(p, (13, 3.5), (21, 2), (21, 11), (13, 11))
        _poly(p, (3, 13), (11, 13), (11, 20.2), (3, 19))
        _poly(p, (13, 13), (21, 13), (21, 22), (13, 20.5))
        return p
    if name == "win_lock":
        p.setFillRule(Qt.FillRule.WindingFill)
        p.addPath(QTransform().scale(.7, .7).map(_build_icon("win")))
        p.addRoundedRect(QRectF(17, 9.5, 6, 5.5), 1, 1)
        shackle = QPainterPath()
        shackle.moveTo(18.2, 9.5)
        shackle.lineTo(18.2, 8)
        shackle.arcTo(QRectF(18.2, 6.2, 3.6, 3.6), 180, -180)
        shackle.lineTo(21.8, 9.5)
        p.addPath(_stroked(shackle, 1.1))
        return p
    if name.startswith("tri_"):
        points = {"tri_up": ((12, 5), (20, 19), (4, 19)), "tri_down": ((12, 19), (4, 5), (20, 5)),
                  "tri_left": ((5, 12), (19, 4), (19, 20)),
                  "tri_right": ((19, 12), (5, 4), (5, 20))}[name]
        _poly(p, *points)
        return p
    if name == "mark":
        _poly(p, (12, 6.2), (17.2, 9), (17.2, 15), (12, 17.8), (6.8, 15), (6.8, 9))
        return p
    if name == "back":
        _poly(p, (20, 12), (5, 12), close=False)
        _poly(p, (10, 7), (5, 12), (10, 17), close=False)
    elif name == "enter":
        p.moveTo(20, 6)
        p.lineTo(20, 12)
        p.quadTo(20, 14, 18, 14)
        p.lineTo(5, 14)
        _poly(p, (9, 10), (5, 14), (9, 18), close=False)
    elif name == "mic":
        p.addRoundedRect(QRectF(9, 3, 6, 12), 3, 3)
        p.moveTo(6, 11)
        p.arcTo(QRectF(6, 5, 12, 12), 180, 180)
        _poly(p, (12, 17), (12, 21), close=False)
    elif name in ("volume_up", "volume_down", "mute"):
        _speaker(p)
        if name == "mute":
            _poly(p, (16, 9), (21, 15), close=False)
            _poly(p, (21, 9), (16, 15), close=False)
        else:
            p.arcMoveTo(QRectF(12, 8, 8, 8), 50)
            p.arcTo(QRectF(12, 8, 8, 8), 50, -100)
            if name == "volume_up":
                p.arcMoveTo(QRectF(9, 4, 16, 16), 50)
                p.arcTo(QRectF(9, 4, 16, 16), 50, -100)
    elif name == "plus":
        _poly(p, (12, 5), (12, 19), close=False)
        _poly(p, (5, 12), (19, 12), close=False)
    elif name == "more":
        for x in (5, 12, 19):
            p.addEllipse(QPointF(x, 12), .4, .4)
    elif name == "undo":
        _poly(p, (9, 14), (4, 9), (9, 4), close=False)
        p.moveTo(4, 9)
        p.lineTo(14, 9)
        p.arcTo(QRectF(8, 9, 12, 12), 90, -180)
        p.lineTo(11, 21)
    elif name == "check":
        _poly(p, (20, 6), (9, 17), (4, 12), close=False)
    elif name == "sun":
        p.addEllipse(QPointF(12, 12), 4.6, 4.6)
        for a, b in (((12, 1.5), (12, 3.5)), ((12, 20.5), (12, 22.5)), ((1.5, 12), (3.5, 12)),
                     ((20.5, 12), (22.5, 12)), ((4.6, 4.6), (6, 6)), ((18, 18), (19.4, 19.4)),
                     ((4.6, 19.4), (6, 18)), ((18, 6), (19.4, 4.6))):
            _poly(p, a, b, close=False)
    elif name == "search":
        p.addEllipse(QPointF(11, 11), 7.5, 7.5)
        _poly(p, (21, 21), (16.5, 16.5), close=False)
    elif name == "brush":
        _poly(p, (19.5, 3.5), (20.5, 4.5), (12.5, 13.5), (10.5, 11.5))
        p.moveTo(10, 13)
        p.cubicTo(7, 13, 6.5, 15.5, 6, 17)
        p.cubicTo(5.7, 18.5, 4.5, 19.5, 3.5, 20)
        p.cubicTo(7, 21, 11.5, 20, 12, 16)
        p.closeSubpath()
    elif name == "fill":
        p.moveTo(12, 3)
        p.cubicTo(8, 8, 5.5, 11, 5.5, 14.5)
        p.cubicTo(5.5, 18.5, 8.5, 21, 12, 21)
        p.cubicTo(15.5, 21, 18.5, 18.5, 18.5, 14.5)
        p.cubicTo(18.5, 11, 16, 8, 12, 3)
        p.closeSubpath()
    else:
        raise KeyError(name)
    return _stroked(p)


@lru_cache(maxsize=None)
def icon_path(name):
    """A filled QPainterPath for an icon, in a 24-unit box."""
    return _build_icon(name)


def _icon_pixmap(name, color, size, ratio):
    pix = QPixmap(round(size * ratio), round(size * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    p.fillPath(icon_path(name), QColor(color))
    p.end()
    return pix


def icon(name, color=TEXT, size=18, checked_color=None):
    screen = QGuiApplication.primaryScreen()
    ratio = max(2.0, screen.devicePixelRatio() if screen else 1.0)
    out = QIcon()
    out.addPixmap(_icon_pixmap(name, color, size, ratio), QIcon.Mode.Normal, QIcon.State.Off)
    out.addPixmap(_icon_pixmap(name, checked_color or color, size, ratio),
                  QIcon.Mode.Normal, QIcon.State.On)
    return out


def palette():
    pal = QPalette()
    roles = {QPalette.ColorRole.Window: SURFACE, QPalette.ColorRole.WindowText: TEXT,
             QPalette.ColorRole.Base: "#121016", QPalette.ColorRole.AlternateBase: SURFACE,
             QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: SURFACE_HOVER,
             QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.BrightText: "#FFFFFF",
             QPalette.ColorRole.ToolTipBase: SURFACE, QPalette.ColorRole.ToolTipText: TEXT,
             QPalette.ColorRole.Highlight: "#FFFFFF",
             QPalette.ColorRole.HighlightedText: "#111111",
             QPalette.ColorRole.PlaceholderText: TEXT_SECONDARY,
             QPalette.ColorRole.Link: ACCENT}
    for role, value in roles.items():
        pal.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5E5966"))
    return pal


def stylesheet():
    load_fonts()
    sans = _loaded.get(SANS) or "sans-serif"
    mono = _loaded.get(MONO) or "monospace"
    return f"""
    * {{ font-family: "{sans}", sans-serif; }}
    QMainWindow, QDialog, QMessageBox {{ background: {SURFACE}; color: {TEXT}; }}
    QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
    QLabel {{ color: {TEXT}; background: transparent; }}
    QLabel#muted {{ color: {TEXT_SECONDARY}; font-size: 12px; }}
    QLabel#appName {{ font-size: 15px; font-weight: 600; }}
    QLabel#pageTitle {{ font-size: 34px; font-weight: 600; }}
    QLabel#cardName {{ font-size: 16px; font-weight: 600; }}
    QLabel#badge {{ background: {ACCENT}; color: #16061A; border-radius: 5px;
                    padding: 3px 8px; font-size: 11px; font-weight: 700; }}
    QLabel#unsaved {{ color: {ACCENT}; font-size: 12px; font-weight: 500; }}

    QPushButton#chip {{ background: rgba(255, 255, 255, 10); color: #BDB7C6;
                        border: 1px solid rgba(255, 255, 255, 18); border-radius: 13px;
                        padding: 4px 12px; font-size: 12px; }}
    QPushButton#chip:hover {{ background: rgba(255, 255, 255, 24); color: {TEXT}; }}
    QPushButton#chip:checked {{ background: rgba(255, 255, 255, 38); color: #FFFFFF;
                                border-color: rgba(255, 255, 255, 90); }}

    QFrame#dock {{ background: rgba(26, 23, 32, 235); border: 1px solid {BORDER};
                   border-radius: 20px; }}
    QFrame#divider {{ background: rgba(255, 255, 255, 23); }}
    QFrame#popover {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
    QLineEdit#hex {{ background: transparent; border: none; color: {TEXT}; padding: 0;
                     font-family: "{mono}", monospace; font-size: 20px; font-weight: 500; }}
    QLineEdit#search {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 11px;
                        padding: 8px 12px; color: {TEXT}; font-size: 14px; }}
    QLineEdit#search:focus {{ border-color: rgba(255, 255, 255, 80); }}
    QLineEdit {{ background: #121016; color: {TEXT}; border: 1px solid {BORDER};
                 border-radius: 6px; padding: 5px; }}

    QPushButton, QToolButton#iconButton {{ background: rgba(255, 255, 255, 18); color: {TEXT};
        border: none; border-radius: 11px; padding: 9px 16px; font-size: 13px; font-weight: 600; }}
    QPushButton:hover, QToolButton#iconButton:hover {{ background: rgba(255, 255, 255, 34); }}
    QPushButton:disabled, QToolButton#iconButton:disabled {{ color: #5E5966; }}
    QToolButton#iconButton {{ padding: 9px; }}
    QToolButton#iconButton:checked {{ background: #FFFFFF; }}
    QToolButton#iconButton:checked:hover {{ background: #E4E1E9; }}
    QToolButton#iconButton::menu-indicator {{ image: none; }}
    QPushButton#primary {{ background: #FFFFFF; color: #111111; }}
    QPushButton#primary:hover {{ background: #E4E1E9; }}

    QSlider::groove:horizontal {{ height: 4px; background: rgba(255, 255, 255, 38);
                                  border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: #FFFFFF; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px;
                                  margin: -5px 0; border-radius: 7px; }}

    QMenu {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: rgba(255, 255, 255, 30); }}
    QMenu::item:disabled {{ color: #5E5966; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 40); border-radius: 4px;
                                   min-height: 30px; margin: 1px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QFrame#card {{ background: #131118; border: 1px solid {BORDER}; border-radius: 18px; }}
    QFrame#card[active="false"]:hover {{ background: #1A1720;
                                         border: 1px solid rgba(255, 255, 255, 90); }}
    QFrame#card[active="false"]:focus {{ border: 1px solid #FFFFFF; }}
    QFrame#card[active="true"] {{ border: 2px solid {ACCENT}; }}
    QFrame#newCard {{ background: transparent; border: 1px dashed rgba(255, 255, 255, 45);
                      border-radius: 18px; }}
    QFrame#newCard:hover {{ background: rgba(255, 255, 255, 8); }}
    """


def apply(app):
    load_fonts()
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(font(SANS, 13))
    app.setStyleSheet(stylesheet())
