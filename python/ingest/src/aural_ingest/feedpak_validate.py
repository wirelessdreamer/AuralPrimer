"""Offline feedpak conformance validation against the vendored JSON Schemas.

Thin wrapper over ``jsonschema`` (Draft 2020-12). The vendored feedpak schemas
(``packages/feedpak/schemas/``) only use internal ``$defs`` ``$ref``s, so each
schema validates standalone with no cross-file resolution.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

# packages/feedpak/schemas relative to the repo root. Resolved from this file:
# src/aural_ingest/feedpak_validate.py -> repo root is 4 parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_DIR = _REPO_ROOT / "packages" / "feedpak" / "schemas"


def schema_dir() -> Path:
    """Return the vendored feedpak schema directory."""
    return _SCHEMA_DIR


@lru_cache(maxsize=None)
def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def validate(instance: Any, schema_name: str) -> None:
    """Validate ``instance`` against the named vendored schema.

    Raises ``jsonschema.ValidationError`` on the first failure.
    """
    Draft202012Validator(_load_schema(schema_name)).validate(instance)


def iter_errors(instance: Any, schema_name: str) -> list[str]:
    """Return human-readable validation error messages (empty if valid)."""
    validator = Draft202012Validator(_load_schema(schema_name))
    return [
        f"{'/'.join(str(p) for p in err.absolute_path)}: {err.message}"
        for err in validator.iter_errors(instance)
    ]
