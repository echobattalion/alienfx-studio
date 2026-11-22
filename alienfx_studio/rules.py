"""Which profile a game uses: its own link first, then its game type."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .profiles import ProfileError, Store, slugify

VERSION = 1
DEFAULT_TYPES = (
    ("Automation", ("Automation",)),
    ("Survival", ("Open World Survival Craft", "Survival")),
    ("Roguelike", ("Roguelike", "Roguelite", "Action Roguelike", "Roguelike Deckbuilder")),
    ("Shooter", ("Looter Shooter", "FPS", "Shooter")),
    ("RPG", ("RPG", "Action RPG", "MMORPG")),
    ("Adventure", ("Point & Click", "Adventure")),
)


class RulesError(ProfileError):
    pass


def _optional_text(value, where):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise RulesError(f"{where}: invalid value {value!r}")
    return value


@dataclass
class GameType:
    name: str
    tags: tuple
    profile: str = None


@dataclass
class GameRule:
    profile: str = None
    type: str = None


@dataclass
class Rules:
    enabled: bool = True
    types: list = field(default_factory=lambda: [GameType(n, t) for n, t in DEFAULT_TYPES])
    games: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d):
        try:
            if d["version"] != VERSION:
                raise RulesError(f"unsupported rules version {d['version']}")
            if not isinstance(d["enabled"], bool):
                raise RulesError("enabled must be true or false")
            types = []
            for t in d["types"]:
                name = _optional_text(t["name"], "type name")
                if name is None or any(x.name.lower() == name.lower() for x in types):
                    raise RulesError(f"invalid or duplicate type {name!r}")
                tags = tuple(_optional_text(tag, f"type {name}") for tag in t["tags"])
                if None in tags:
                    raise RulesError(f"type {name}: invalid tag")
                types.append(GameType(name, tags, _optional_text(t.get("profile"), name)))
            games = {}
            for key, g in d["games"].items():
                games[key] = GameRule(_optional_text(g.get("profile"), key),
                                      _optional_text(g.get("type"), key))
            return cls(d["enabled"], types, games)
        except (KeyError, TypeError, AttributeError) as e:
            raise RulesError(f"malformed rules: {e}") from e

    def to_dict(self):
        games = {}
        for key in sorted(self.games):
            rule = self.games[key]
            entry = {k: v for k, v in (("profile", rule.profile), ("type", rule.type)) if v}
            if entry:
                games[key] = entry
        return {"version": VERSION, "enabled": self.enabled,
                "types": [{"name": t.name, "tags": list(t.tags), "profile": t.profile}
                          for t in self.types],
                "games": games}

    def type_named(self, name):
        for game_type in self.types:
            if game_type.name.lower() == name.lower():
                return game_type
        return None

    def type_of(self, key, tags):
        """A per-game override, else the first type in list order matching a tag."""
        rule = self.games.get(key)
        if rule and rule.type:
            chosen = self.type_named(rule.type)
            if chosen:
                return chosen
        have = {tag.lower() for tag in tags}
        for game_type in self.types:
            if have.intersection(tag.lower() for tag in game_type.tags):
                return game_type
        return None

    def profile_for(self, key, tags, exists):
        """Return (profile, reason), or None when the game has no usable profile."""
        if not self.enabled:
            return None
        rule = self.games.get(key)
        if rule and rule.profile and exists(rule.profile):
            return rule.profile, "linked game"
        game_type = self.type_of(key, tags)
        if game_type and game_type.profile and exists(game_type.profile):
            return game_type.profile, f"{game_type.name} type"
        return None

    def rename_profile(self, old, new):
        wanted = slugify(old)
        changed = False
        for holder in [*self.types, *self.games.values()]:
            if holder.profile and slugify(holder.profile) == wanted:
                holder.profile = new
                changed = True
        return changed


def path(root):
    return Path(root) / "rules.json"


def load(root):
    try:
        return Rules.from_dict(json.loads(path(root).read_text()))
    except FileNotFoundError:
        return Rules()
    except ValueError as e:
        raise RulesError(f"malformed rules: {e}") from e


def save(root, rules):
    Store._write(path(root), rules.to_dict())


def rename_profile(root, old, new):
    """Keep links working after a rename. A malformed file is left for the user to fix."""
    try:
        rules = load(root)
    except RulesError:
        return
    if rules.rename_profile(old, new):
        save(root, rules)
