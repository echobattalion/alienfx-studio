"""Profile model and on-disk store (~/.config/alienfx)."""
import colorsys
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import kbmap
from .layout import ELC_ZONES, TOUCHPAD_ZONES

VERSION = 1
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ProfileError(Exception):
    pass


def to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def to_rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _color(value, where):
    if not isinstance(value, str) or not _HEX.match(value):
        raise ProfileError(f"{where}: invalid color {value!r}")
    return value.upper()


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"


@dataclass
class Profile:
    name: str
    brightness: int = 100
    kb_default: str = "#FF00FF"
    keys: dict = field(default_factory=dict)
    elc_default: str = "#FF00FF"
    zones: dict = field(default_factory=dict)   # int zone -> hex

    @classmethod
    def from_dict(cls, d):
        try:
            if d["version"] != VERSION:
                raise ProfileError(f"unsupported version {d['version']}")
            name = d["name"]
            if not isinstance(name, str) or not name.strip():
                raise ProfileError("empty name")
            bright = d.get("brightness", 100)
            if not isinstance(bright, int) or not 0 <= bright <= 100:
                raise ProfileError(f"invalid brightness {bright!r}")
            keys = {k: _color(v, f"key {k}") for k, v in d["keyboard"]["keys"].items()}
            zones = {}
            for z, v in d["elc"]["zones"].items():
                zone = int(z)
                if not 0 <= zone < ELC_ZONES:
                    raise ProfileError(f"invalid zone {z}")
                zones[zone] = _color(v, f"zone {z}")
            return cls(name.strip(), bright, _color(d["keyboard"]["default"], "keyboard"),
                       keys, _color(d["elc"]["default"], "elc"), zones)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ProfileError(f"malformed profile: {e}") from e

    def to_dict(self):
        return {"version": VERSION, "name": self.name, "brightness": self.brightness,
                "keyboard": {"default": self.kb_default, "keys": dict(sorted(self.keys.items()))},
                "elc": {"default": self.elc_default,
                        "zones": {str(z): self.zones[z] for z in sorted(self.zones)}}}

    def copy(self):
        return Profile.from_dict(self.to_dict())

    def color_of(self, item_id):
        kind, ref = item_id.split(":", 1)
        if kind == "K":
            return self.keys.get(ref, self.kb_default)
        return self.zones.get(int(ref), self.elc_default)

    def set_color(self, item_id, value):
        kind, ref = item_id.split(":", 1)
        if kind == "K":
            self.keys[ref] = value
        else:
            self.zones[int(ref)] = value

    def keyboard_colors(self):
        """RGB for all keyboard indices; unmapped indices take the default."""
        out = [to_rgb(self.kb_default)] * kbmap.N
        for name, value in self.keys.items():
            for i in kbmap.KEYS.get(name, ()):
                out[i] = to_rgb(value)
        return out

    def elc_colors(self):
        return [to_rgb(self.zones.get(z, self.elc_default)) for z in range(ELC_ZONES)]


def default_profile():
    """The starter scheme: colored key groups and a rainbow touchpad."""
    keys = {}
    for names, value in ((kbmap.FKEYS, "#0000FF"), (kbmap.ARROWS, "#0000FF"),
                         (kbmap.NUMS, "#FFFF00"), (kbmap.MODS, "#00FF00"),
                         (kbmap.WASD, "#FF0000")):
        keys.update({name: value for name in names})
    n = len(TOUCHPAD_ZONES)
    zones = {z: to_hex(tuple(round(c * 255) for c in colorsys.hsv_to_rgb(
        (i / (n - 1)) * 280 / 360, 1, 1))) for i, z in enumerate(TOUCHPAD_ZONES)}
    return Profile("Default", 100, "#FF00FF", keys, "#FF00FF", zones)


class Store:
    def __init__(self, root=None):
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        self.root = Path(root) if root else Path(base) / "alienfx"
        self.dir = self.root / "profiles"
        self.state = self.root / "state.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        if not any(self.dir.glob("*.json")):
            first = default_profile()
            self.save(first)
            self.set_active(first.name)

    @staticmethod
    def _write(path, data):
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def list(self):
        """Return valid profiles as [(slug, name)], sorted by name."""
        out = []
        for path in self.dir.glob("*.json"):
            try:
                out.append((path.stem, Profile.from_dict(json.loads(path.read_text())).name))
            except (OSError, ValueError, ProfileError) as e:
                print(f"alienfx-studio: skipping {path.name}: {e}", file=sys.stderr)
        return sorted(out, key=lambda p: p[1].lower())

    def resolve(self, name):
        wanted = slugify(name)
        for slug, pname in self.list():
            if slug == wanted or pname.lower() == name.lower():
                return slug
        raise ProfileError(f"no such profile: {name}")

    def exists(self, name):
        return (self.dir / f"{slugify(name)}.json").exists()

    def load(self, name):
        slug = self.resolve(name)
        return Profile.from_dict(json.loads((self.dir / f"{slug}.json").read_text()))

    def save(self, profile):
        self._write(self.dir / f"{slugify(profile.name)}.json", profile.to_dict())

    def delete(self, name):
        slug = self.resolve(name)
        if len(self.list()) <= 1:
            raise ProfileError("cannot delete the last profile")
        (self.dir / f"{slug}.json").unlink()

    def rename(self, old, new):
        slug = self.resolve(old)
        new_slug = slugify(new)
        if new_slug != slug and self.exists(new):
            raise ProfileError(f"a profile named {new!r} already exists")
        profile = self.load(old)
        was_active = self.active_name() == profile.name
        profile.name = new.strip()
        self.save(profile)
        if new_slug != slug:
            (self.dir / f"{slug}.json").unlink()
        if was_active:
            self.set_active(profile.name)
        return profile

    def active_name(self):
        profiles = self.list()
        if not profiles:
            raise ProfileError("no valid profiles found")
        try:
            wanted = json.loads(self.state.read_text())["active"]
            return self.load(wanted).name
        except (OSError, ValueError, KeyError, TypeError, ProfileError):
            return profiles[0][1]

    def set_active(self, name):
        self._write(self.state, {"active": name})
