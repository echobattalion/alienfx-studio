"""Read installed Steam games and their store tags from Steam's local files."""
import json
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

_APPINFO_V41 = 0x07564429
_MAX_DEPTH = 64
_TAGS_FILE = Path(__file__).parent / "assets" / "steam_tags.json"
_ROOTS = ("~/.local/share/Steam", "~/.steam/steam",
          "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")
_PATH = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')
_APPID = re.compile(r'"appid"\s+"(\d+)"')
_NAME = re.compile(r'"name"\s+"((?:[^"\\]|\\.)*)"')


STEAM_RUNNING, STEAM_STOPPED, STEAM_UNKNOWN = "running", "stopped", "unknown"


class SteamError(Exception):
    pass


@dataclass(frozen=True)
class Game:
    app_id: int
    name: str
    tags: tuple = ()

    @property
    def key(self):
        return f"steam:{self.app_id}"


def find_root():
    override = os.environ.get("ALIENFX_STEAM_ROOT")
    candidates = [override] if override else [os.path.expanduser(r) for r in _ROOTS]
    for candidate in candidates:
        if (Path(candidate) / "steamapps").is_dir():
            return Path(candidate).resolve()
    return None


def pid_file():
    """Steam writes its process ID here each time it starts."""
    return Path(os.environ.get("ALIENFX_STEAM_PID") or "~/.steam/steam.pid").expanduser()


def steam_state(path=None, proc="/proc"):
    """Whether Steam runs. Unknown means its PID file cannot be used as a signal."""
    try:
        pid = int(Path(path or pid_file()).read_text().strip())
    except (OSError, ValueError):
        return STEAM_UNKNOWN
    try:
        with open(f"{proc}/{pid}/comm") as f:
            return STEAM_RUNNING if f.read().strip() == "steam" else STEAM_STOPPED
    except OSError:
        return STEAM_STOPPED


def _unescape(text):
    return re.sub(r"\\(.)", r"\1", text)


def _library_dirs(root):
    dirs = [root / "steamapps"]
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(errors="replace")
    except OSError:
        return dirs
    for raw in _PATH.findall(text):
        candidate = Path(_unescape(raw)) / "steamapps"
        if candidate.is_dir() and candidate.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(candidate)
    return dirs


def installed_apps(root):
    """Return {app_id: name} from the app manifests in every library folder."""
    apps = {}
    for directory in _library_dirs(root):
        for manifest in directory.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(errors="replace")
            except OSError:
                continue
            appid, name = _APPID.search(text), _NAME.search(text)
            if appid and name:
                apps[int(appid.group(1))] = _unescape(name.group(1))
    return apps


class _Reader:
    """Binary VDF with keys stored as indices into a shared string table."""

    def __init__(self, data, strings):
        self.data = data
        self.strings = strings

    def parse(self, p, depth=0):
        if depth > _MAX_DEPTH:
            raise ValueError("nesting too deep")
        data, out = self.data, {}
        while True:
            kind = data[p]
            p += 1
            if kind == 8:
                return out, p
            key = self.strings[struct.unpack_from("<I", data, p)[0]]
            p += 4
            if kind == 0:
                out[key], p = self.parse(p, depth + 1)
            elif kind == 1:
                end = data.index(b"\0", p)
                out[key] = data[p:end].decode("utf-8", "replace")
                p = end + 1
            elif kind == 2:
                out[key] = struct.unpack_from("<i", data, p)[0]
                p += 4
            elif kind == 7:
                out[key] = struct.unpack_from("<Q", data, p)[0]
                p += 8
            else:
                raise ValueError(f"unknown value type {kind}")


def read_appinfo(path, wanted):
    """Return {app_id: (type, (tag ids...))} for the wanted apps."""
    try:
        data = Path(path).read_bytes()
        if struct.unpack_from("<I", data, 0)[0] != _APPINFO_V41:
            raise SteamError("unsupported appinfo.vdf version")
        table = struct.unpack_from("<q", data, 8)[0]
        count = struct.unpack_from("<I", data, table)[0]
        strings, p = [], table + 4
        for _ in range(count):
            end = data.index(b"\0", p)
            strings.append(data[p:end].decode("utf-8", "replace"))
            p = end + 1
        reader, out, p = _Reader(data, strings), {}, 16
        while p < table:
            app_id, size = struct.unpack_from("<II", data, p)
            if app_id == 0:
                break
            if app_id in wanted:
                info = reader.parse(p + 68)[0]
                common = info.get("appinfo", {}).get("common", {})
                tags = common.get("store_tags") or {}
                out[app_id] = (str(common.get("type", "")).lower(),
                               tuple(int(v) for v in tags.values()))
            p += 8 + size
        return out
    except (OSError, ValueError, IndexError, struct.error, AttributeError, TypeError) as e:
        raise SteamError(f"cannot read appinfo.vdf: {e}") from e


def tag_names():
    """Steam's tag ID to English name list, bundled so lookups stay offline."""
    try:
        return {int(k): v for k, v in json.loads(_TAGS_FILE.read_text()).items()}
    except (OSError, ValueError, AttributeError) as e:
        print(f"alienfx-studio: cannot read Steam tag names: {e}", file=sys.stderr)
        return {}


class Library:
    def __init__(self, root=None):
        self.root = Path(root) if root else find_root()

    def watch_dirs(self):
        """Directories whose changes can alter the game list."""
        if self.root is None:
            return []
        dirs = [*_library_dirs(self.root), self.root / "appcache"]
        return [d for d in dirs if d.is_dir()]

    def stamp(self):
        """Changes when games are added or removed, or Steam updates its metadata.

        Manifest times are left out because they change constantly during a download.
        """
        if self.root is None:
            return None
        times = []
        for path in (self.root / "appcache" / "appinfo.vdf",
                     self.root / "steamapps" / "libraryfolders.vdf"):
            try:
                times.append(path.stat().st_mtime_ns)
            except OSError:
                times.append(None)
        manifests = frozenset(str(path) for directory in _library_dirs(self.root)
                              for path in directory.glob("appmanifest_*.acf"))
        return manifests, tuple(times)

    def games(self):
        """Installed games by name. Tools like Proton are left out when metadata exists."""
        if self.root is None:
            return []
        apps = installed_apps(self.root)
        try:
            info = read_appinfo(self.root / "appcache" / "appinfo.vdf", set(apps))
        except SteamError as e:      # direct game links still work without tags
            print(f"alienfx-studio: {e}", file=sys.stderr)
            info = {}
        names = tag_names()
        out = []
        for app_id, name in apps.items():
            kind, ids = info.get(app_id, ("game", ()))
            if kind == "game":
                out.append(Game(app_id, name, tuple(names[i] for i in ids if i in names)))
        return sorted(out, key=lambda g: g.name.lower())

    def game(self, app_id):
        for game in self.games():
            if game.app_id == app_id:
                return game
        return Game(app_id, f"Steam app {app_id}")
