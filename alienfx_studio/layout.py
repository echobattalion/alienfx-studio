"""Physical geometry and legends for the x17 keyboard and ELC zones, in key-width units."""
from dataclasses import dataclass

from . import kbmap

ELC_ZONES = 20
TOUCHPAD_ZONES = list(range(2, 10))
REAR_ZONES = list(range(10, 20))
WIDTH = 16.0
KEYBOARD_HEIGHT = 5.7
HEIGHT = 6.95


@dataclass(frozen=True)
class Item:
    id: str                # "K:<key name>" or "E:<zone>"
    label: str
    x: float
    y: float
    w: float
    h: float
    enabled: bool = True
    legend: str = ""       # text, or an icon name when kind is "icon" or "nav"
    secondary: str = ""
    # alpha, symbol, function, modifier, top_modifier, icon, top_icon, nav, zone
    kind: str = "alpha"
    homing: bool = False


LABELS = {"LSHIFT": "Shift", "RSHIFT": "Shift", "LCTRL": "Ctrl", "RCTRL": "Ctrl",
          "LWIN": "Win", "RWIN": "Windows lock", "LALT": "Alt", "RALT": "Alt", "FN": "Fn",
          "CAPS": "Caps", "TAB": "Tab", "ENTER": "Enter", "BKSP": "Backspace",
          "ESC": "Esc", "HOME": "Home", "END": "End", "DEL": "Del", "SPACE": "Space",
          "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
          "MEDIA1": "Mic mute", "MEDIA2": "Volume up", "MEDIA3": "Volume down",
          "MEDIA4": "Audio mute"}

PHYSICAL_ROWS = [  # (y, height, [(key name, width), ...])
    (0.0, 0.7, [("ESC", 1)] + [(f"F{i}", 1) for i in range(1, 13)]
     + [("HOME", 1), ("END", 1), ("DEL", 1)]),
    (0.7, 1.0, [("`", 1)] + [(c, 1) for c in "1234567890-="]
     + [("BKSP", 2), ("MEDIA1", 1)]),
    (1.7, 1.0, [("TAB", 1.5)] + [(c, 1) for c in "QWERTYUIOP[]"]
     + [("\\", 1.5), ("MEDIA2", 1)]),
    (2.7, 1.0, [("CAPS", 1.75)] + [(c, 1) for c in "ASDFGHJKL;'"]
     + [("ENTER", 2.25), ("MEDIA3", 1)]),
    (3.7, 1.0, [("LSHIFT", 2.25)] + [(c, 1) for c in "ZXCVBNM,./"]
     + [("RSHIFT", 1.75), ("UP", 1), ("MEDIA4", 1)]),
    (4.7, 1.0, [("LCTRL", 1.25), ("FN", 1), ("LWIN", 1), ("LALT", 1), ("SPACE", 5),
                ("RALT", 1), ("RWIN", 1), ("RCTRL", 1.75),
                ("LEFT", 1), ("DOWN", 1), ("RIGHT", 1)]),
]

_SECONDARY = dict(zip("`1234567890-=[]\\;',./", "~!@#$%^&*()_+{}|:\"<>?"))
# Function-row icons are approximate: the reference photo is low resolution.
_FUNCTION_SECONDARY = {"F1": "◠", "F2": "M", "F3": "▥", "F4": "C", "F5": "▭", "F6": "▦",
                       "F7": "☁", "F8": "▣", "F9": "E", "F10": "⚙", "F11": "☀", "F12": "⋯",
                       "HOME": "PRT SCR", "END": "PAUSE", "DEL": "INSERT"}
_MODIFIERS = {"TAB": "TAB", "CAPS": "CAPS", "LSHIFT": "SHIFT", "RSHIFT": "SHIFT",
              "LCTRL": "CTRL", "RCTRL": "CTRL"}
_TOP_MODIFIERS = {"FN": "FN", "LALT": "ALT", "RALT": "ALT"}
_ICONS = {"BKSP": "back", "ENTER": "enter",
          "MEDIA1": "mic", "MEDIA2": "volume_up", "MEDIA3": "volume_down", "MEDIA4": "mute"}
_TOP_ICONS = {"LWIN": "win", "RWIN": "win_lock"}   # aligned with the FN and ALT legends
_NAV = {"UP": ("tri_up", "PAGE UP"), "DOWN": ("tri_down", "PAGE DN"),
        "LEFT": ("tri_left", ""), "RIGHT": ("tri_right", "")}

# (title, x) captions drawn above the ELC strip.
SECTIONS = [("Power", 0.0), ("Other", 1.6), ("Touchpad", 3.0), ("Rear ring and bars", 8.2)]
SECTION_Y = 5.9
_ELC_Y = 6.25
_ELC_H = 0.7


def _describe(name):
    """Return (legend, secondary, kind) for a key."""
    if name == "ESC" or name in _FUNCTION_SECONDARY:
        return name, _FUNCTION_SECONDARY.get(name, ""), "function"
    if name in _MODIFIERS:
        return _MODIFIERS[name], "", "modifier"
    if name in _TOP_MODIFIERS:
        return _TOP_MODIFIERS[name], "", "top_modifier"
    if name in _TOP_ICONS:
        return _TOP_ICONS[name], "", "top_icon"
    if name in _ICONS:
        return _ICONS[name], "", "icon"
    if name in _NAV:
        return _NAV[name][0], _NAV[name][1], "nav"
    if name == "SPACE":
        return "", "", "alpha"
    if name in _SECONDARY:
        return name, _SECONDARY[name], "symbol"
    return name, "", "alpha"


def _key(name, x, y, w, h=1.0):
    legend, secondary, kind = _describe(name)
    return Item(f"K:{name}", LABELS.get(name, name), x, y, w, h, name in kbmap.KEYS,
                legend, secondary, kind, name in ("S", "F", "J"))


def _zone(zone, label, x, w):
    return Item(f"E:{zone}", label, x, _ELC_Y, w, _ELC_H, kind="zone")


def build_items():
    items = []
    for y, h, row in PHYSICAL_ROWS:
        x = 0.0
        for name, w in row:
            items.append(_key(name, x, y, w, h))
            x += w
    items.append(_zone(0, "Power", 0.0, 1.4))
    items.append(_zone(1, "Other", 1.6, 1.1))
    items += [_zone(z, f"Touchpad {i + 1}", 3.0 + i * .62, .62)
              for i, z in enumerate(TOUCHPAD_ZONES)]
    items += [_zone(z, f"Rear {i + 1}", 8.2 + i * .78, .78)
              for i, z in enumerate(REAR_ZONES)]
    return items


def key_ids(names):
    return {f"K:{n}" for n in names}


GROUPS = {
    "WASD": key_ids(kbmap.WASD),
    "Numbers": key_ids(kbmap.NUMS),
    "F-keys": key_ids(kbmap.FKEYS),
    "Mods": key_ids(kbmap.MODS),
    "Arrows": key_ids(kbmap.ARROWS),
    "Touchpad": {f"E:{z}" for z in TOUCHPAD_ZONES},
    "Rear": {f"E:{z}" for z in REAR_ZONES},
}
