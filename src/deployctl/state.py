"""Local Deployment State Cache for instant diffing.

Stores the manifest of the last successful deployment so subsequent diffs
can be computed in ~0.02s without making hundreds of sequential FTP network calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / ".deployctl" / "state"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def get_state_file(project: str, environment: str) -> Path:
    _ensure_state_dir()
    safe_name = f"{project}_{environment}".replace("/", "_").replace("\\", "_")
    return STATE_DIR / f"{safe_name}.json"


def load_deployment_state(project: str, environment: str) -> dict[str, Any] | None:
    """Load the state manifest of the last deployment."""
    state_file = get_state_file(project, environment)
    if not state_file.exists():
        return None

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "files" in data:
                return data
    except Exception:
        pass
    return None


def save_deployment_state(
    project: str,
    environment: str,
    local_files: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Save the current deployed file manifest to local state cache."""
    state_file = get_state_file(project, environment)
    _ensure_state_dir()

    manifest: dict[str, dict[str, Any]] = {}
    for rel_path, info in local_files.items():
        size = getattr(info, "size", None) if not isinstance(info, dict) else info.get("size")
        mtime = getattr(info, "mtime", None) if not isinstance(info, dict) else info.get("mtime")
        manifest[rel_path] = {
            "size": size,
            "mtime": mtime,
        }

    payload = {
        "project": project,
        "environment": environment,
        "updated_at": time.time(),
        "files_count": len(manifest),
        "files": manifest,
        "metadata": metadata or {},
    }

    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def clear_deployment_state(project: str, environment: str) -> bool:
    """Clear state cache for a project environment."""
    state_file = get_state_file(project, environment)
    if state_file.exists():
        try:
            state_file.unlink()
            return True
        except Exception:
            return False
    return True
