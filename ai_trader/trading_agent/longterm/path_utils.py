"""Path helpers for Windows-safe artifact writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def write_json_artifact(path: str | Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON artifact, supporting long Windows scheduler paths."""
    write_text_artifact(path, json.dumps(payload, indent=2, sort_keys=True))


def read_json_artifact(path: str | Path) -> Any:
    """Read a JSON artifact, supporting long Windows scheduler paths."""
    return json.loads(read_text_artifact(path))


def read_text_artifact(path: str | Path) -> str:
    """Read a text artifact, supporting long Windows scheduler paths."""
    return _long_path(Path(path)).read_text(encoding="utf-8")


def artifact_exists(path: str | Path) -> bool:
    """Return whether an artifact exists, supporting long Windows scheduler paths."""
    return _long_path(Path(path)).exists()


def artifact_is_dir(path: str | Path) -> bool:
    """Return whether an artifact is a directory, supporting long Windows scheduler paths."""
    return _long_path(Path(path)).is_dir()


def write_text_artifact(path: str | Path, text: str) -> None:
    """Write a text artifact, supporting long Windows scheduler paths."""
    output = Path(path)
    _long_path(output.parent).mkdir(parents=True, exist_ok=True)
    _long_path(output).write_text(text, encoding="utf-8")


def _long_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    text = str(path.resolve())
    if text.startswith("\\\\?\\"):
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)
