"""Git context of the directory being deployed.

deployctl ships whatever is checked out in `local_path`, so the branch, commit and dirty state are
recorded with every deployment and checked against the target's policy before anything is uploaded.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# Branches that do not trigger the "unexpected branch" warning when a target has no `allowed_branches`.
DEFAULT_BRANCHES = ("main", "master", "develop")


def _git(path: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_git_context(path: Path) -> dict[str, Any]:
    """Describe the git state of `path`.

    Returns `{"available": False}` outside a git repository. Otherwise `branch` (`"HEAD"` when detached),
    the short `commit`, and `dirty_files`, the number of changed or untracked files inside `path` only.
    """
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return {"available": False}

    status = _git(path, "status", "--porcelain", "--", ".") or ""
    return {
        "available": True,
        "branch": branch,
        "commit": _git(path, "rev-parse", "--short", "HEAD") or "",
        "dirty_files": len([line for line in status.splitlines() if line.strip()]),
    }


def evaluate_git_policy(
    git: dict[str, Any],
    env_cfg: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """Check the git state against the target's policy.

    Target options (in projects.yaml): `allowed_branches` (list) and `require_clean` (bool).

    Args:
        git: Result of `get_git_context()`.
        env_cfg: The target's configuration.
        previous: Git context recorded by the last successful deployment, if any.

    Returns:
        `(warnings, blockers)`. Blockers stop a real deployment unless forced; warnings never do.
    """
    warnings: list[str] = []
    blockers: list[str] = []

    if not git.get("available"):
        return warnings, blockers

    branch = git["branch"]
    allowed = env_cfg.get("allowed_branches") or []

    if allowed:
        if branch not in allowed:
            blockers.append(f"Branch '{branch}' is not allowed for this target (allowed: {', '.join(allowed)})")
    elif branch not in DEFAULT_BRANCHES:
        warnings.append(f"Deploying from branch '{branch}', not one of {', '.join(DEFAULT_BRANCHES)}")

    if git["dirty_files"]:
        message = f"{git['dirty_files']} uncommitted change(s) in the deploy directory"
        if env_cfg.get("require_clean"):
            blockers.append(message + " (this target requires a clean tree)")
        else:
            warnings.append(message)

    if previous and previous.get("available") and previous.get("branch") != branch:
        warnings.append(
            f"The last deployment was from branch '{previous.get('branch')}' ({previous.get('commit')}); "
            f"this one is from '{branch}' ({git['commit']}). Files only in the other branch may remain on the server."
        )

    return warnings, blockers
