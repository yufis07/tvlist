"""Tiny settings file (~/.tvlist/settings.json). Passwords are never stored."""

from __future__ import annotations

import json
from pathlib import Path

DIR = Path.home() / ".tvlist"


def load() -> dict:
    try:
        return json.loads((DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    data = {k: v for k, v in data.items() if k != "password"}
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        (DIR / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def aliases_path() -> Path:
    """Optional {"local folder name": "PoGDesign show name"} overrides."""
    return DIR / "aliases.json"
