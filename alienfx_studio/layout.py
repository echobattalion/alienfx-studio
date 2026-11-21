"""Visual geometry for the keyboard and ELC zones, in key-width units."""
from dataclasses import dataclass

from . import kbmap

ELC_ZONES = 20
TOUCHPAD_ZONES = list(range(2, 10))
REAR_ZONES = list(range(10, 20))
WIDTH = 15.5


@dataclass(frozen=True)
class Item:
    id: str                # "K:<key name>" or "E:<zone>"
    label: str
    x: float
    y: float
    w: float
    h: float
    enabled: bool = True


LABELS = {"LSHIFT": "Shift", "RSHIFT": "Shift", "LCTRL": "Ctrl", "RCTRL": "Ctrl",
          "LWIN": "Win", "RWIN": "Win", "LALT": "Alt", "RALT": "Alt", "FN": "Fn",
          "CAPS": "Caps", "TAB": "Tab", "ENTER": "Enter", "BKSP": "Bksp",
          "ESC": "Esc", "HOME": "Home", "END": "End", "DEL": "Del", "SPACE": "",
          "UP": "▲", "DOWN": "▼", "LEFT": "◀", "RIGHT": "▶"}

_ROWS = [
    [("`", 1)] + [(c, 1) for c in "1234567890-="] + [("BKSP", 2.5)],
    [("TAB", 1.5)] + [(c, 1) for c in "QWERTYUIOP[]"] + [("\\", 2)],
    [("CAPS", 1.75)] + [(c, 1) for c in "ASDFGHJKL;'"] + [("ENTER", 2.75)],
    [("LSHIFT", 2.25)] + [(c, 1) for c in "ZXCVBNM,./"] + [("RSHIFT", 3.25)],
    [("LCTRL", 1.25), ("FN", 1), ("LWIN", 1.25), ("LALT", 1.25), ("SPACE", 4.25),
     ("RALT", 1.25), ("RWIN", 1), ("RCTRL", 1.25)],
]
_TOP = ["ESC"] + [f"F{i}" for i in range(1, 13)] + ["HOME", "END", "DEL"]
_TOP_H = 0.75
_ELC_Y = 6.35

# (title, x) captions drawn above the ELC strip.
SECTIONS = [("Power", 0.0), ("Other", 1.6), ("Touchpad", 3.0), ("Rear ring and bars", 8.2)]
SECTION_Y = 5.95
HEIGHT = _ELC_Y + 0.8


def _key(name, x, y, w, h=1.0):
    return Item(f"K:{name}", LABELS.get(name, name), x, y, w, h, name in kbmap.KEYS)


def build_items():
    items = []
    top_w = WIDTH / len(_TOP)
    items += [_key(n, i * top_w, 0, top_w, _TOP_H) for i, n in enumerate(_TOP)]
    for r, row in enumerate(_ROWS):
        x = 0.0
        for name, w in row:
            items.append(_key(name, x, _TOP_H + r, w))
            x += w
    y = _TOP_H + 4
    items += [_key("UP", 13.5, y, 1, .5), _key("LEFT", 12.5, y + .5, 1, .5),
              _key("DOWN", 13.5, y + .5, 1, .5), _key("RIGHT", 14.5, y + .5, 1, .5)]

    items.append(Item("E:0", "Pwr", 0, _ELC_Y, 1.4, .7))
    items.append(Item("E:1", "1", 1.6, _ELC_Y, 1.1, .7))
    items += [Item(f"E:{z}", str(z), 3.0 + i * .6, _ELC_Y, .6, .7)
              for i, z in enumerate(TOUCHPAD_ZONES)]
    items += [Item(f"E:{z}", str(z), 8.2 + i * .73, _ELC_Y, .73, .7)
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
