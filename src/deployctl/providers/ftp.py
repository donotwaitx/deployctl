"""FTP deployment provider using Python's built-in ftplib.

Keeps credentials in-memory only. Avoids process arguments or command leaks.
"""

from __future__ import annotations

import os
import posixpath
from ftplib import FTP, error_perm
from pathlib import Path
from typing import Any, Callable

from deployctl.providers.base import BaseProvider, RemoteFileInfo


class FTPProvider(BaseProvider):
    """Standard FTP Provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ftp: FTP | None = None

    def connect(self) -> None:
        if self.ftp:
            return
        port = self.port or 21
        self.ftp = FTP(timeout=self.timeout)
        self.ftp.connect(self.host, port)
        self.ftp.login(self.username, self.password or "")
        self.ftp.set_pasv(True)

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.connect()
            welcome = self.ftp.getwelcome() if self.ftp else "Connected"
            return True, f"Successfully connected to FTP {self.host}:{self.port or 21}. {welcome[:60]}"
        except Exception as e:
            return False, f"FTP connection error: {str(e)}"
        finally:
            self.close()

    def _full_remote_path(self, rel_path: str) -> str:
        clean_rel = rel_path.lstrip("/")
        if self.remote_path == "/" or not self.remote_path:
            return f"/{clean_rel}"
        return f"{self.remote_path}/{clean_rel}"

    def ensure_remote_dir(self, remote_rel_dir: str) -> None:
        """Create remote directory structure recursively if not exists."""
        if not self.ftp:
            self.connect()

        full_dir = self._full_remote_path(remote_rel_dir)
        parts = [p for p in full_dir.split("/") if p]
        current = ""

        for part in parts:
            current = f"{current}/{part}"
            try:
                self.ftp.cwd(current)
            except error_perm:
                try:
                    self.ftp.mkd(current)
                    self.ftp.cwd(current)
                except error_perm:
                    pass

    def list_remote(self, sub_path: str = "") -> dict[str, RemoteFileInfo]:
        """Recursively scan remote directory using MLSD (or NLST fallback)."""
        if not self.ftp:
            self.connect()

        results: dict[str, RemoteFileInfo] = {}
        base_dir = self._full_remote_path(sub_path)

        def _traverse(current_dir: str, rel_prefix: str) -> None:
            try:
                # Try modern MLSD first
                items = list(self.ftp.mlsd(current_dir))
                for name, facts in items:
                    if name in (".", ".."):
                        continue
                    item_type = facts.get("type", "file")
                    item_rel = f"{rel_prefix}/{name}" if rel_prefix else name
                    item_remote = f"{current_dir}/{name}"

                    if item_type == "dir":
                        _traverse(item_remote, item_rel)
                    else:
                        size = int(facts.get("size", 0))
                        mtime_str = facts.get("modify")
                        results[item_rel] = RemoteFileInfo(
                            rel_path=item_rel,
                            size=size,
                            mtime=None,
                            is_dir=False,
                        )
            except Exception:
                # Fallback to NLST traversal
                try:
                    names = self.ftp.nlst(current_dir)
                    for full_item in names:
                        name = posixpath.basename(full_item)
                        if name in (".", "..") or not name:
                            continue
                        item_rel = f"{rel_prefix}/{name}" if rel_prefix else name
                        
                        # Try cd into it to determine if directory
                        try:
                            self.ftp.cwd(full_item)
                            _traverse(full_item, item_rel)
                        except error_perm:
                            # It is a file
                            size = 0
                            try:
                                size = self.ftp.size(full_item) or 0
                            except Exception:
                                pass
                            results[item_rel] = RemoteFileInfo(
                                rel_path=item_rel,
                                size=size,
                                is_dir=False,
                            )
                except Exception:
                    pass

        try:
            _traverse(base_dir, "")
        except Exception:
            pass

        return results

    def upload_file(
        self,
        local_path: Path,
        remote_rel_path: str,
        callback: Callable[[int], None] | None = None,
    ) -> bool:
        if not self.ftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        parent_dir = posixpath.dirname(full_remote)
        if parent_dir:
            self.ensure_remote_dir(posixpath.dirname(remote_rel_path))

        with open(local_path, "rb") as f:
            def _cb(chunk: bytes):
                if callback:
                    callback(len(chunk))

            self.ftp.storbinary(f"STOR {full_remote}", f, blocksize=32768, callback=_cb)

        return True

    def download_file(self, remote_rel_path: str, local_path: Path) -> bool:
        if not self.ftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        with open(local_path, "wb") as f:
            self.ftp.retrbinary(f"RETR {full_remote}", f.write)

        return True

    def delete_file(self, remote_rel_path: str) -> bool:
        if not self.ftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        try:
            self.ftp.delete(full_remote)
            return True
        except Exception:
            return False

    def list_dir(self, remote_dir: str = "") -> list[dict[str, Any]]:
        """List contents of remote_dir (files and directories)."""
        if not self.ftp:
            self.connect()

        target = remote_dir.strip() if remote_dir else ""
        if not target:
            try:
                target = self.ftp.pwd() or "/"
            except Exception:
                target = "/"

        if not target.startswith("/"):
            target = "/" + target
        target = posixpath.normpath(target)

        results: list[dict[str, Any]] = []

        # 1. Try modern MLSD first
        try:
            items = list(self.ftp.mlsd(target))
            for name, facts in items:
                if name in (".", ".."):
                    continue
                is_dir = facts.get("type") in ("dir", "cdir", "pdir")
                size = int(facts.get("size", 0)) if not is_dir else 0
                full_path = posixpath.normpath(posixpath.join(target, name))
                if not full_path.startswith("/"):
                    full_path = "/" + full_path
                results.append({
                    "name": name,
                    "path": full_path,
                    "is_dir": is_dir,
                    "size": size,
                })
            results.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return results
        except Exception:
            pass

        # 2. Fallback to LIST with cwd
        orig_pwd = None
        try:
            orig_pwd = self.ftp.pwd()
        except Exception:
            pass

        try:
            self.ftp.cwd(target)
            curr = self.ftp.pwd() or target
        except Exception:
            curr = target

        lines: list[str] = []
        try:
            self.ftp.dir(lines.append)
        except Exception:
            pass

        if lines:
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue
                parts = line_str.split(None, 8)
                if len(parts) >= 9:
                    perms = parts[0]
                    is_dir = perms.startswith("d")
                    name = parts[8].strip()
                    if name in (".", ".."):
                        continue
                    try:
                        size = int(parts[4]) if not is_dir else 0
                    except (ValueError, IndexError):
                        size = 0
                    full_path = posixpath.normpath(posixpath.join(curr, name))
                    if not full_path.startswith("/"):
                        full_path = "/" + full_path
                    results.append({
                        "name": name,
                        "path": full_path,
                        "is_dir": is_dir,
                        "size": size,
                    })
                elif "<DIR>" in line_str:
                    win_parts = line_str.split("<DIR>", 1)
                    name = win_parts[1].strip()
                    if name in (".", ".."):
                        continue
                    full_path = posixpath.normpath(posixpath.join(curr, name))
                    if not full_path.startswith("/"):
                        full_path = "/" + full_path
                    results.append({
                        "name": name,
                        "path": full_path,
                        "is_dir": True,
                        "size": 0,
                    })
        else:
            try:
                names = self.ftp.nlst()
                for name in names:
                    basename = posixpath.basename(name)
                    if basename in (".", "..") or not basename:
                        continue
                    is_dir = False
                    try:
                        self.ftp.cwd(basename)
                        is_dir = True
                        self.ftp.cwd("..")
                    except Exception:
                        is_dir = False
                    full_path = posixpath.normpath(posixpath.join(curr, basename))
                    if not full_path.startswith("/"):
                        full_path = "/" + full_path
                    results.append({
                        "name": basename,
                        "path": full_path,
                        "is_dir": is_dir,
                        "size": 0,
                    })
            except Exception:
                pass

        if orig_pwd:
            try:
                self.ftp.cwd(orig_pwd)
            except Exception:
                pass

        results.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return results

    def create_dir(self, remote_dir: str) -> bool:
        """Create remote directory structure recursively."""
        if not self.ftp:
            self.connect()

        target = remote_dir.strip()
        if not target.startswith("/"):
            target = "/" + target
        target = posixpath.normpath(target)
        parts = [p for p in target.split("/") if p]
        current = ""

        for part in parts:
            current = f"{current}/{part}"
            try:
                self.ftp.cwd(current)
            except error_perm:
                try:
                    self.ftp.mkd(current)
                    self.ftp.cwd(current)
                except error_perm:
                    pass
        return True

    def close(self) -> None:
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None
