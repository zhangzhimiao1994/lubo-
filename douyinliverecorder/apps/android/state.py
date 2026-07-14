from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_STATUS = {
    "monitoring": False,
    "active_recordings": 0,
    "message": "Stopped",
}


def read_status(path: str | Path) -> dict[str, Any]:
    status_path = Path(path)
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_STATUS)
    if not isinstance(data, dict):
        return dict(DEFAULT_STATUS)
    return {**DEFAULT_STATUS, **data}


def write_status(path: str | Path, status: dict[str, Any]) -> None:
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_suffix(f"{status_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(status, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(status_path)
