"""Structured outcome of a deployment, so callers (the MCP server above all) see what actually happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Cap on file names listed per category in `to_dict()`, so a first full deploy cannot flood an agent's context.
MAX_LISTED_FILES = 200


@dataclass
class DeployReport:
    """Everything a deployment run learned; filled in by `run_deployment`."""

    project: str = ""
    environment: str = ""
    remote_path: str = ""
    dry_run: bool = False
    status: str = "NOT_RUN"
    ok: bool = False
    message: str = ""
    diff_source: str = ""
    git: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged_count: int = 0
    uploaded: int = 0
    failed_uploads: list[str] = field(default_factory=list)
    failed_deletes: list[str] = field(default_factory=list)
    post_deploy_deleted: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def finish(self, ok: bool, status: str, message: str = "") -> bool:
        """Record the final outcome and return `ok`, so callers can `return report.finish(...)`."""
        self.ok = ok
        self.status = status
        self.message = message
        return ok

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary; file lists are capped at MAX_LISTED_FILES with a `truncated` flag."""
        lists = {"added": self.added, "modified": self.modified, "deleted": self.deleted}
        return {
            "status": self.status,
            "ok": self.ok,
            "message": self.message,
            "project": self.project,
            "environment": self.environment,
            "remote_path": self.remote_path,
            "dry_run": self.dry_run,
            "diff_source": self.diff_source,
            "git": self.git,
            "warnings": self.warnings,
            "counts": {
                "added": len(self.added),
                "modified": len(self.modified),
                "deleted": len(self.deleted),
                "unchanged": self.unchanged_count,
                "uploaded": self.uploaded,
            },
            "files": {name: files[:MAX_LISTED_FILES] for name, files in lists.items()},
            "truncated": any(len(files) > MAX_LISTED_FILES for files in lists.values()),
            "failed_uploads": self.failed_uploads,
            "failed_deletes": self.failed_deletes,
            "post_deploy_deleted": self.post_deploy_deleted,
            "duration_seconds": round(self.duration_seconds, 2),
        }
