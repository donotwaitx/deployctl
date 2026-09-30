"""Audit logging and history tracking for deployctl.

Ensures logs never store passwords or secrets.
Maintains deployment history in ~/.deployctl/logs/.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from rich.console import Console

from deployctl.config import LOGS_DIR, ensure_config_dirs
from deployctl.security import sanitize_text

console = Console()
error_console = Console(stderr=True)


class DeployLogger:
    """Logger that sanitizes output and records deployment audits."""

    def __init__(self, project: str, environment: str, secrets_to_mask: list[str] | None = None):
        ensure_config_dirs()
        self.project = project
        self.environment = environment
        self.secrets = [s for s in (secrets_to_mask or []) if s]
        self.timestamp = datetime.datetime.now()
        timestamp_str = self.timestamp.strftime("%Y%m%d_%H%M%S")
        self.log_file = LOGS_DIR / f"deploy_{project}_{environment}_{timestamp_str}.log"
        self._entries: list[str] = []

    def log(self, message: str, level: str = "INFO") -> None:
        """Write sanitized message to file buffer."""
        clean_msg = sanitize_text(message, self.secrets)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] [{level.upper()}] {clean_msg}"
        self._entries.append(entry)
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception:
            pass

    def info(self, message: str) -> None:
        self.log(message, "INFO")

    def warning(self, message: str) -> None:
        self.log(message, "WARNING")

    def error(self, message: str) -> None:
        self.log(message, "ERROR")

    def finalize(
        self,
        status: str,
        files_count: int,
        duration_seconds: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record structured summary in history index and log file."""
        summary = {
            "project": self.project,
            "environment": self.environment,
            "timestamp": self.timestamp.isoformat(),
            "status": status,
            "files_count": files_count,
            "duration_seconds": round(duration_seconds, 2),
            "log_file": str(self.log_file),
            "details": details or {},
        }
        self.log(f"Deployment finished with status {status}. Summary: {json.dumps(summary)}")

        history_file = LOGS_DIR / "history.json"
        history: list[dict] = []
        if history_file.exists():
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    history = json.load(f)
                    if not isinstance(history, list):
                        history = []
            except Exception:
                history = []

        history.insert(0, summary)
        # Keep latest 500 entries
        history = history[:500]

        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception:
            pass


def get_deployment_history(project: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Retrieve past deployment records."""
    ensure_config_dirs()
    history_file = LOGS_DIR / "history.json"
    if not history_file.exists():
        return []

    try:
        with open(history_file, "r", encoding="utf-8") as f:
            records = json.load(f)
            if not isinstance(records, list):
                return []

            if project:
                records = [r for r in records if r.get("project", "").lower() == project.lower()]

            return records[:limit]
    except Exception:
        return []
