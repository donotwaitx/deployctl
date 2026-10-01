"""Local filesystem provider for deployctl.

Useful for local deployments, testing, staging environments, and mocking.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

from deployctl.providers.base import BaseProvider, RemoteFileInfo


class LocalProvider(BaseProvider):
    """Local Filesystem Provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dest_root = Path(self.remote_path).expanduser().resolve()

    def connect(self) -> None:
        self.dest_root.mkdir(parents=True, exist_ok=True)

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.connect()
            return True, f"Local directory accessible: {self.dest_root}"
        except Exception as e:
            return False, f"Local directory error: {str(e)}"

    def ensure_remote_dir(self, remote_rel_dir: str) -> None:
        target = self.dest_root / remote_rel_dir
        target.mkdir(parents=True, exist_ok=True)

    def list_remote(self, sub_path: str = "") -> dict[str, RemoteFileInfo]:
        results: dict[str, RemoteFileInfo] = {}
        scan_dir = self.dest_root / sub_path if sub_path else self.dest_root
        if not scan_dir.exists():
            return results

        for root, _, files in os.walk(scan_dir):
            for f in files:
                full_f = Path(root) / f
                rel = os.path.relpath(full_f, self.dest_root).replace("\\", "/")
                try:
                    st = full_f.stat()
                    results[rel] = RemoteFileInfo(
                        rel_path=rel,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        is_dir=False,
                    )
                except OSError:
                    pass
        return results

    def upload_file(
        self,
        local_path: Path,
        remote_rel_path: str,
        callback: Callable[[int], None] | None = None,
    ) -> bool:
        dest_file = self.dest_root / remote_rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_file)
        if callback:
            callback(dest_file.stat().st_size)
        return True

    def download_file(self, remote_rel_path: str, local_path: Path) -> bool:
        src_file = self.dest_root / remote_rel_path
        if not src_file.exists():
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, local_path)
        return True

    def delete_file(self, remote_rel_path: str) -> bool:
        target = self.dest_root / remote_rel_path
        try:
            if target.is_file():
                target.unlink()
                return True
        except OSError:
            pass
        return False

    def list_dir(self, remote_dir: str = "") -> list[dict[str, Any]]:
        self.connect()
        clean = remote_dir.strip()
        # Like the FTP/SFTP providers, accept an absolute path that already includes the base directory
        if clean.startswith(str(self.dest_root)):
            clean = clean[len(str(self.dest_root)):]
        clean = clean.lstrip("/")
        target = (self.dest_root / clean).resolve() if clean else self.dest_root
        if not target.exists() or not target.is_dir():
            return []

        results: list[dict[str, Any]] = []
        try:
            for item in target.iterdir():
                is_dir = item.is_dir()
                try:
                    rel_path = "/" + str(item.relative_to(self.dest_root)).replace("\\", "/")
                except ValueError:
                    rel_path = "/" + item.name
                results.append({
                    "name": item.name,
                    "path": rel_path,
                    "is_dir": is_dir,
                    "size": item.stat().st_size if not is_dir else 0,
                    "mtime": item.stat().st_mtime,
                })
        except OSError:
            pass

        results.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return results

    def create_dir(self, remote_dir: str) -> bool:
        self.connect()
        clean = remote_dir.strip().lstrip("/")
        target = self.dest_root / clean
        try:
            target.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def close(self) -> None:
        pass
