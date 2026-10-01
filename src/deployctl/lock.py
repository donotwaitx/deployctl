"""Per-target deployment lock, so two deployments to the same target cannot run at once."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

from deployctl.config import DEPLOYCTL_HOME

try:
    import fcntl
except ImportError:  # Windows: no advisory locks, deployments are not serialised
    fcntl = None  # type: ignore[assignment]

LOCKS_DIR = DEPLOYCTL_HOME / "locks"


@contextmanager
def deploy_lock(project: str, environment: str) -> Iterator[bool]:
    """Hold an exclusive lock on `project:environment` for the duration of the block.

    Yields `True` once the lock is held, or `False` right away when another deployment holds it. The lock is
    released by the OS if the process dies, so it can never go stale.
    """
    if fcntl is None:
        yield True
        return

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{project}_{environment}".lower())
    with open(LOCKS_DIR / f"{name}.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
