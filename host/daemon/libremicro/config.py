"""Config loading, validation, v1 migration, and export/import.

The JSON config is the single source of truth: the CLI, the daemon, and the web UI all
read and write this one document, and the published schema (host/config/schema.json) is
what makes it safe for an AI to generate. So this module is deliberately strict about
validation and deliberately forgiving about older documents.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .color import Palette
from .layout import Layout
from .palettes import BUILTIN

try:
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover - validation degrades to a warning
    jsonschema = None

HERE = Path(__file__).resolve()
# .../host/daemon/libremicro/config.py -> parents[2] is host/
REPO_CONFIG_DIR = HERE.parents[2] / "config"
SCHEMA_PATH = REPO_CONFIG_DIR / "schema.json"
EXAMPLE_PATH = REPO_CONFIG_DIR / "example.json"

USER_CONFIG_PATH = Path(os.path.expanduser("~/.config/libremicro/config.json"))

#: Search order at startup. The shipped example is last and acts as a template — see
#: `Config.save_path` for why edits never land back on it.
DEFAULT_CONFIG_PATHS = (
    USER_CONFIG_PATH,
    REPO_CONFIG_DIR / "config.json",
    EXAMPLE_PATH,
)


class ConfigError(Exception):
    """Config is unusable. The message is intended to be shown to the user verbatim."""


# --- v1 -> v2 migration -----------------------------------------------------

def _v1_binding(token: str) -> dict:
    """A v1 encoder action string becomes a v2 binding object."""
    if token.startswith("shell:"):
        return {"shell": token[len("shell:"):].strip()}
    return {"action": token}


def _v1_encoder(spec: dict | None) -> dict | None:
    if not spec:
        return None
    return {k: _v1_binding(v) for k, v in spec.items() if isinstance(v, str)}


def migrate_v1(old: dict) -> dict:
    """Upgrade a v1 config to v2. v1 had no version field, one implicit profile, and
    encoder/key actions as bare strings."""
    keys: list[dict] = []
    for k in old.get("keys", []):
        new: dict[str, Any] = {"index": k["index"]}
        for field in ("label", "color", "watch"):
            if field in k:
                new[field] = k[field]
        if "mode" in k:
            new["on"] = {"press": {"mode": k["mode"]}}
        elif "launch" in k:
            new["on"] = {"press": {"launch": k["launch"]}}
        elif "command" in k:
            new["on"] = {"press": {"shell": k["command"]}}
        keys.append(new)

    modes: dict[str, dict] = {}
    for name, m in (old.get("modes") or {}).items():
        out: dict[str, Any] = {"encoder": _v1_encoder(m.get("encoder")) or {}}
        if "activate_key" in m:
            out["activate_key"] = m["activate_key"]
        if "flash" in m:
            out["flash"] = m["flash"]
        modes[name] = out

    profile: dict[str, Any] = {"label": "Migrated from v1", "keys": keys}
    if modes:
        profile["modes"] = modes
    enc = _v1_encoder(old.get("default_encoder"))
    if enc:
        profile["encoder"] = enc

    device: dict[str, Any] = {}
    if "port" in old:
        device["port"] = old["port"]
    if "brightness" in old:
        device["brightness"] = old["brightness"]

    out: dict[str, Any] = {"version": 2, "active_profile": "default",
                           "profiles": {"default": profile}}
    if device:
        out["device"] = device
    return out


def normalize(raw: dict) -> dict:
    """Bring any accepted document up to v2 shape. Does not validate."""
    doc = dict(raw)
    doc.pop("$schema", None)
    if doc.get("version") != 2:
        doc = migrate_v1(doc)
    doc.setdefault("device", {})
    doc.setdefault("profiles", {"default": {}})
    if not doc.get("active_profile"):
        doc["active_profile"] = ("default" if "default" in doc["profiles"]
                                 else next(iter(doc["profiles"])))
    return doc


# --- validation -------------------------------------------------------------

def load_schema() -> dict | None:
    try:
        return json.loads(SCHEMA_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def validate(doc: dict) -> list[str]:
    """Return a list of human-readable validation errors ([] means valid).

    With jsonschema unavailable this returns a single advisory line rather than failing
    closed — a missing dev dependency shouldn't stop someone using their macropad.
    """
    schema = load_schema()
    if schema is None:
        return ["schema.json not found; skipped validation"]
    if jsonschema is None:
        return ["jsonschema not installed (pip install jsonschema); skipped validation"]

    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return errors


# --- the resolved config ----------------------------------------------------

class Config:
    """A validated config plus the objects derived from it."""

    def __init__(self, doc: dict, path: Path | None = None):
        self.doc = normalize(doc)
        self.path = path
        self.layout = Layout(self.doc.get("layout"))
        self.palettes: dict[str, Palette] = dict(BUILTIN)
        for name, spec in (self.doc.get("palettes") or {}).items():
            try:
                self.palettes[name] = Palette.from_config(spec)
            except (KeyError, ValueError) as exc:
                raise ConfigError(f"palette {name!r}: {exc}") from exc

    # --- accessors ---------------------------------------------------------

    @property
    def device(self) -> dict:
        return self.doc.get("device") or {}

    @property
    def port(self) -> str:
        return self.device.get("port", "auto")

    @property
    def baud(self) -> int:
        return int(self.device.get("baud", 115200))

    @property
    def brightness(self) -> int:
        return int(self.device.get("brightness", 200))

    @property
    def fps(self) -> float:
        return float(self.device.get("fps", 30))

    @property
    def power(self) -> dict:
        return self.doc.get("power") or {}

    @property
    def webui(self) -> dict:
        return self.doc.get("webui") or {}

    @property
    def profile_names(self) -> list[str]:
        return list((self.doc.get("profiles") or {}).keys())

    @property
    def active_profile_name(self) -> str:
        return self.doc["active_profile"]

    def profile(self, name: str | None = None) -> dict:
        name = name or self.active_profile_name
        profiles = self.doc.get("profiles") or {}
        if name not in profiles:
            raise ConfigError(f"no profile named {name!r}; have {sorted(profiles)}")
        return profiles[name] or {}

    # --- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        candidates = [Path(path)] if path else list(DEFAULT_CONFIG_PATHS)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                raw = json.loads(candidate.read_text())
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{candidate}: invalid JSON — {exc}") from exc
            cfg = cls(raw, candidate)
            errors = validate(cfg.doc)
            hard = [e for e in errors if not e.endswith("skipped validation")]
            if hard:
                raise ConfigError(
                    f"{candidate} failed schema validation:\n  " + "\n  ".join(hard))
            return cfg
        searched = "\n  ".join(str(c) for c in candidates)
        raise ConfigError(f"no config found. Looked in:\n  {searched}")

    @property
    def save_path(self) -> Path:
        """Where edits should be written.

        Never the shipped example: it's a template that ships in the repo, and the web UI
        writing to it would both clobber it and show up as a dirty git tree. A config that
        was loaded from the example graduates to the user's own config path on first save.
        """
        if self.path is None or self.path == EXAMPLE_PATH:
            return USER_CONFIG_PATH
        return self.path

    def save(self, path: str | os.PathLike | None = None) -> Path:
        target = Path(path) if path else self.save_path
        if target is None:
            raise ConfigError("no path to save to")
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = {"$schema": _relative_schema(target), **self.doc}
        # Write to a sibling temp file then rename, so a crash mid-write can't leave the
        # user with a truncated config and a pad that won't start.
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
        tmp.replace(target)
        self.path = target
        return target

    def export_bundle(self) -> dict:
        """A self-contained document another CM2 owner can import.

        Palettes referenced by effects are inlined, so a bundle that uses a built-in
        palette still renders identically if the built-in corpus later changes.
        """
        bundle = json.loads(json.dumps(self.doc))
        used = _referenced_palettes(self.doc)
        inlined = dict(bundle.get("palettes") or {})
        for name in used:
            if name not in inlined and name in BUILTIN:
                inlined[name] = BUILTIN[name].to_config()
        if inlined:
            bundle["palettes"] = inlined
        return bundle

    @classmethod
    def import_bundle(cls, data: dict) -> "Config":
        cfg = cls(data)
        errors = [e for e in validate(cfg.doc) if not e.endswith("skipped validation")]
        if errors:
            raise ConfigError("bundle failed validation:\n  " + "\n  ".join(errors))
        return cfg


def _relative_schema(target: Path) -> str:
    try:
        return os.path.relpath(SCHEMA_PATH, target.parent)
    except ValueError:
        return str(SCHEMA_PATH)


def _referenced_palettes(doc: dict) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "name" in node and "palette" in node and isinstance(node["palette"], str):
                found.add(node["palette"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return found
