from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    type: str
    id: str
    progress: float
    message: str | None = None
    artifact: str | None = None

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "type": self.type,
            "id": self.id,
            "progress": self.progress,
        }
        if self.message is not None:
            d["message"] = self.message
        if self.artifact is not None:
            d["artifact"] = self.artifact
        return json.dumps(d, sort_keys=True)


def emit(event: ProgressEvent) -> None:
    """Emit one JSONL progress event to stdout."""
    sys.stdout.write(event.to_json() + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Human readable logs to stderr."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
