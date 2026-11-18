"""Change detection and dry-run diff engine for deployctl.

Scans local working tree, excludes unwanted files/patterns,
and compares with remote state.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable
from pathlib import Path
from typing import Any

import pathspec


# Files up to this size get a content hash (local scan, state cache and the PHP scan bridge all use it); larger
# files, which are rarely source code, are compared by size and modification time only.
HASH_MAX_BYTES = 1024 * 1024


@dataclass
class LocalFileInfo:
    rel_path: str
    abs_path: Path
    size: int
    mtime: float
    sha1: str | None = None  # filled lazily by file_sha1()


def file_sha1(info: LocalFileInfo) -> str | None:
    """SHA-1 of a local file's content, or `None` when it is over HASH_MAX_BYTES or unreadable.

    The digest is cached on `info`, so diffing and saving the state cache read each file once.
    """
    if info.size > HASH_MAX_BYTES:
        return None
    if info.sha1 is None:
        try:
            info.sha1 = hashlib.sha1(info.abs_path.read_bytes()).hexdigest()
        except OSError:
            return None
    return info.sha1


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


def _normalize_pattern(pattern: str) -> str:
    """Legacy `dir/**` means "the directory and everything in it": rewrite it to the gitignore form `dir`."""
    if pattern.endswith("/**") and len(pattern) > 3 and not pattern.startswith("#"):
        return pattern[:-3]
    return pattern


@lru_cache(maxsize=32)
def _compile_patterns(patterns: tuple[str, ...]) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines("gitignore", [_normalize_pattern(p) for p in patterns])


def is_excluded(rel_path: str, exclude_patterns: list[str], is_dir: bool = False) -> bool:
    """Check a relative path against exclusion patterns using `.gitignore` semantics.

    Supports `#` comments, `!` negation (last match wins), a leading `/` to anchor to the project root,
    a trailing `/` for directories only, and `*`, `?`, `[a-z]` and `**`.
    """
    if not exclude_patterns:
        return False
    norm_path = rel_path.replace("\\", "/").strip("/")
    if not norm_path or norm_path == ".":
        return False
    if is_dir:
        norm_path += "/"
    return _compile_patterns(tuple(exclude_patterns)).match_file(norm_path)


def read_ignore_file(path: Path) -> list[str]:
    """Read the pattern lines of a `.gitignore`-style file (empty list when it does not exist)."""
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def git_ignored_paths(local_dir: Path) -> set[str] | None:
    """Paths git itself ignores under `local_dir` (nested .gitignore, global ignore, .git/info/exclude).

    Ignored directories are reported once with a trailing `/`. Returns `None` when `local_dir` is not
    inside a git work tree or git is unavailable. Tracked files are never reported, as in git.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(local_dir), "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
            capture_output=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {p for p in out.decode("utf-8", "surrogateescape").split("\0") if p}


def _is_git_ignored(rel_path: str, ignored: set[str]) -> bool:
    if rel_path in ignored or rel_path + "/" in ignored:
        return True
    parts = rel_path.split("/")
    return any("/".join(parts[:i]) + "/" in ignored for i in range(1, len(parts)))


# Never uploaded, whatever the framework rules or `use_gitignore` say.
ALWAYS_EXCLUDE = [".git/", ".env", ".env.*", "node_modules/", ".DS_Store"]

# Paths that git ignores (or the default excludes drop) but a production server needs to run the app.
LARAVEL_INCLUDES = [
    "/vendor/",  # composer dependencies: git ignores them, shared FTP hosting has no composer
    "/public/",  # uploads, thumbs, built assets
    "/storage/**/.gitignore",  # keeps the storage/* skeleton (cache, sessions, views, logs...) without its runtime files
    "/bootstrap/cache/.gitignore",
]


def framework_includes(local_dir: Path) -> list[str]:
    """Include rules for the detected framework (Laravel: `artisan` + `composer.json`), else none."""
    if (local_dir / "artisan").is_file() and (local_dir / "composer.json").is_file():
        return list(LARAVEL_INCLUDES)
    return []


def _may_contain_included(rel_dir: str, include_patterns: list[str]) -> bool:
    """True when an include rule could match something inside `rel_dir` (so the directory must be entered)."""
    roots = [p.strip("/").split("/")[0] for p in include_patterns if not p.startswith("!")]
    return bool(roots) and rel_dir.replace("\\", "/").split("/")[0] in roots


def build_skip(
    exclude_patterns: list[str],
    git_ignored: set[str] | None = None,
    include_patterns: list[str] | None = None,
    final_excludes: list[str] | None = None,
) -> Callable[[str, bool], bool]:
    """Return `skip(rel_path, is_dir)`: True when the path is not part of the deployment.

    Excluded by patterns or git unless an include rule rescues it; `ALWAYS_EXCLUDE` and `final_excludes`
    (the user's `.deployignore`) always win. The same predicate protects remote files from deletion.
    """
    include_patterns = include_patterns or []
    final = ALWAYS_EXCLUDE + (final_excludes or [])

    def skip(rel: str, is_dir: bool) -> bool:
        rel = rel.replace("\\", "/")
        if is_excluded(rel, final, is_dir=is_dir):
            return True
        hit = is_excluded(rel, exclude_patterns, is_dir=is_dir) or bool(git_ignored and _is_git_ignored(rel, git_ignored))
        if not hit:
            return False
        if include_patterns:
            if is_dir:
                return not _may_contain_included(rel, include_patterns)
            return not is_excluded(rel, include_patterns)
        return True

    return skip


def scan_local_files(
    local_dir: Path,
    exclude_patterns: list[str],
    git_ignored: set[str] | None = None,
    include_patterns: list[str] | None = None,
    final_excludes: list[str] | None = None,
    skip: Callable[[str, bool], bool] | None = None,
) -> dict[str, LocalFileInfo]:
    """Scan local directory and return mapping of relative path -> LocalFileInfo."""
    local_dir = local_dir.resolve()
    result: dict[str, LocalFileInfo] = {}

    if not local_dir.exists():
        return result

    skip = skip or build_skip(exclude_patterns, git_ignored, include_patterns, final_excludes)

    for root, dirs, files in os.walk(local_dir):
        root_rel = os.path.relpath(root, local_dir)
        # Prune excluded directories early for performance
        dirs[:] = [d for d in dirs if not skip(os.path.join(root_rel, d) if root_rel != "." else d, True)]

        for file_name in files:
            full_path = Path(root) / file_name
            rel_path = os.path.relpath(full_path, local_dir).replace("\\", "/")

            if skip(rel_path, False):
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
    skip: Callable[[str, bool], bool] | None = None,
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

            remote_sha1 = getattr(remote_info, "sha1", None)
            if remote_sha1 is None and isinstance(remote_info, dict):
                remote_sha1 = remote_info.get("sha1")

            # If sizes differ, or if local file is newer by more than 2 seconds (FAT/FTP timestamp tolerance)
            if remote_size is not None and remote_size != local_info.size:
                diff.modified.append(rel_path)
                diff.total_upload_bytes += local_info.size
            elif remote_sha1 and (local_sha1 := file_sha1(local_info)):
                # Same size and both sides hashed: the content decides, not the timestamp (git checkout
                # rewrites mtimes; an edit that keeps the byte count shows only in the hash)
                if local_sha1 != remote_sha1:
                    diff.modified.append(rel_path)
                    diff.total_upload_bytes += local_info.size
                else:
                    diff.unchanged.append(rel_path)
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
            if (skip and skip(rel_path, False)) or (exclude_patterns and is_excluded(rel_path, exclude_patterns)):
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

