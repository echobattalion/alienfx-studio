#!/usr/bin/env python3
"""Verified light-index map for the Alienware x17 R1 keyboard (Darfon 0d62:babc).

Derived empirically from mod-7 barcode photos. Rows sit on a stride of 20,
but wide keys consume extra indices, so payload keys drift within each row.
Spaces/RWin have no LEDs. Dead indices are simply absent from KEYS.
"""
N = 136

KEYS = {}
# --- row 0, base 0 : ESC F1..F12 HOME END DEL (no wide keys) ---
KEYS["ESC"] = [0]
for i in range(1, 13):
    KEYS[f"F{i}"] = [i]
KEYS["HOME"], KEYS["END"], KEYS["DEL"] = [13], [14], [15]

# --- row 1, base 20 : ` 1..0 - =  BKSP(wide) ---
KEYS["`"] = [20]
for d in range(1, 11):
    KEYS[str(d % 10)] = [20 + d]          # 1->21 ... 9->29, 0->30
KEYS["-"], KEYS["="] = [31], [32]
KEYS["BKSP"] = [33, 34, 35]        # 3 LEDs

# --- row 2, base 40 : TAB(2) Q..P [ ] \ ---
KEYS["TAB"] = [40, 41]
for off, n in enumerate(["Q","W","E","R","T","Y","U","I","O","P","[","]"]):
    KEYS[n] = [42 + off]
KEYS["\\"] = [54, 55]                  # 2 LEDs

# --- row 3, base 60 : CAPS(2) A..L ; ' ENTER(2) ---
KEYS["CAPS"] = [60, 61]
for off, n in enumerate(["A","S","D","F","G","H","J","K","L",";","'"]):
    KEYS[n] = [62 + off]
KEYS["ENTER"] = [73, 74]

# --- row 4, base 80 : LSHIFT(3) Z..M , . / RSHIFT(2) ---
KEYS["LSHIFT"] = [80, 81, 82]
for off, n in enumerate(["Z","X","C","V","B","N","M",",",".","/"]):
    KEYS[n] = [83 + off]
KEYS["RSHIFT"] = [93, 94]

# --- row 5, base 100 : LCTRL FN LWIN(2) LALT [space=no LED] RALT RCTRL ---
KEYS["LCTRL"], KEYS["FN"] = [100], [101]
KEYS["LWIN"] = [102, 103]
KEYS["LALT"] = [104]
KEYS["RALT"], KEYS["RCTRL"] = [111], [112]

# --- nav / arrows ---
KEYS["UP"]   = [114]
# The arrow keys alias across three index banks 7 apart. The mod-7 probe could not
# distinguish them (same colour in every bank), and the probe proved these indices
# drive nothing else -- so set all three banks and the arrows are correct regardless.
KEYS["LEFT"]  = [119, 126, 133]
KEYS["DOWN"]  = [120, 127, 134]
KEYS["RIGHT"] = [121, 128, 135]

# --- semantic groups for the scheme ---
WASD  = ["W", "A", "S", "D"]
NUMS  = [str(d % 10) for d in range(1, 11)]   # digits only; ` - = stay magenta
FKEYS = [f"F{i}" for i in range(1, 13)]
MODS  = ["LSHIFT","RSHIFT","LCTRL","RCTRL","LWIN","LALT","RALT","FN",
         "CAPS","TAB","ENTER","BKSP"]
ARROWS= ["UP","DOWN","LEFT","RIGHT"]

def indices(names):
    out = []
    for n in names:
        out.extend(KEYS[n])
    return out
