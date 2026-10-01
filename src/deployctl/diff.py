"""Change detection and dry-run diff engine for deployctl.

Scans local working tree, excludes unwanted files/patterns,
and compares with remote state.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LocalFileInfo:
    rel_path: str
    abs_path: Path
    size: int
    mtime: float


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    total_upload_bytes: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    @property
    def upload_files(self) -> list[str]:
        return self.added + self.modified


def is_excluded(rel_path: str, exclude_patterns: list[str]) -> bool:
    """Check if a relative path matches any exclusion patterns."""
    # Normalize with forward slashes
    norm_path = rel_path.replace("\\", "/")
    parts = norm_path.split("/")

    for pattern in exclude_patterns:
        # Check against full path
        if fnmatch.fnmatch(norm_path, pattern):
            return True
        # Check against filename / dirname
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
        # Check wildcard directory prefix
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if norm_path == prefix or norm_path.startswith(prefix + "/") or prefix in parts:
                return True

    return False


def scan_local_files(
    local_dir: Path,
    exclude_patterns: list[str],
) -> dict[str, LocalFileInfo]:
    """Scan local directory and return mapping of relative path -> LocalFileInfo."""
    local_dir = local_dir.resolve()
    result: dict[str, LocalFileInfo] = {}

    if not local_dir.exists():
        return result

    for root, dirs, files in os.walk(local_dir):
        # Prune excluded directories early for performance
        root_rel = os.path.relpath(root, local_dir)
        if root_rel != ".":
            if is_excluded(root_rel, exclude_patterns):
                dirs.clear()
                continue

        dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root_rel if root_rel != "." else "", d), exclude_patterns)]

        for file_name in files:
            full_path = Path(root) / file_name
            rel_path = os.path.relpath(full_path, local_dir).replace("\\", "/")

            if is_excluded(rel_path, exclude_patterns):
                continue

            try:
                stat = full_path.stat()
                result[rel_path] = LocalFileInfo(
                    rel_path=rel_path,
                    abs_path=full_path,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            except (OSError, PermissionError):
                continue

    return result


def compute_diff(
    local_files: dict[str, LocalFileInfo],
    remote_files: dict[str, Any],
    detect_deletions: bool = False,
    exclude_patterns: list[str] | None = None,
) -> DiffResult:
    """Compare local files with remote files to determine changes.
    
    remote_files should be a mapping of rel_path -> RemoteFileInfo or dict with 'size' and 'mtime'.
    """
    diff = DiffResult()

    for rel_path, local_info in local_files.items():
        if rel_path not in remote_files:
            diff.added.append(rel_path)
            diff.total_upload_bytes += local_info.size
        else:
            remote_info = remote_files[rel_path]
            remote_size = getattr(remote_info, "size", None)
            if remote_size is None and isinstance(remote_info, dict):
                remote_size = remote_info.get("size")

            # If sizes differ, or if local file is newer by more than 2 seconds (FAT/FTP timestamp tolerance)
            if remote_size is not None and remote_size != local_info.size:
                diff.modified.append(rel_path)
                diff.total_upload_bytes += local_info.size
            else:
                remote_mtime = getattr(remote_info, "mtime", None)
                if remote_mtime is None and isinstance(remote_info, dict):
                    remote_mtime = remote_info.get("mtime")

                if remote_mtime and (local_info.mtime - remote_mtime > 2.0):
                    diff.modified.append(rel_path)
                    diff.total_upload_bytes += local_info.size
                else:
                    diff.unchanged.append(rel_path)

    if detect_deletions:
        for rel_path in remote_files:
            if rel_path in local_files:
                continue
            # Never delete remote files that are excluded from deployment (.env, storage, ...)
            if exclude_patterns and is_excluded(rel_path, exclude_patterns):
                continue
            diff.deleted.append(rel_path)

    diff.added.sort()
    diff.modified.sort()
    diff.deleted.sort()
    diff.unchanged.sort()

    return diff


def format_diff_text(diff: DiffResult, is_dry_run: bool = True, remote_path: str = "", max_display: int = 25) -> str:
    """Format diff summary string matching Section 8 format."""
    lines: list[str] = []
    if is_dry_run:
        lines.append("DRY RUN")
    if remote_path:
        lines.append(f"Target Remote Directory: {remote_path}\n")
    else:
        lines.append("")

    if diff.added:
        lines.append(f"Added ({len(diff.added)} files):")
        for f in diff.added[:max_display]:
            lines.append(f"  + {f}")
        if len(diff.added) > max_display:
            lines.append(f"  ... and {len(diff.added) - max_display} more added files")
        lines.append("")

    if diff.modified:
        lines.append(f"Modified ({len(diff.modified)} files):")
        for f in diff.modified[:max_display]:
            lines.append(f"  ~ {f}")
        if len(diff.modified) > max_display:
            lines.append(f"  ... and {len(diff.modified) - max_display} more modified files")
        lines.append("")

    if diff.deleted:
        lines.append(f"Deleted ({len(diff.deleted)} files):")
        for f in diff.deleted[:max_display]:
            lines.append(f"  - {f}")
        if len(diff.deleted) > max_display:
            lines.append(f"  ... and {len(diff.deleted) - max_display} more deleted files")
        lines.append("")

    if not diff.has_changes:
        lines.append("No changes detected. Working tree is in sync with remote.\n")

    lines.append("Total Changes:")
    lines.append(f"  +{len(diff.added)} added")
    lines.append(f"  ~{len(diff.modified)} modified")
    lines.append(f"  -{len(diff.deleted)} deleted")
    lines.append(f"  ={len(diff.unchanged)} unchanged\n")

    if is_dry_run:
        lines.append("No files uploaded.")

    return "\n".join(lines)

