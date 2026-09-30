"""SFTP deployment provider using paramiko.

Supports SSH password authentication and SSH private key authentication.
Credentials stay in-memory only.
"""

from __future__ import annotations

import os
import posixpath
import stat
from pathlib import Path
from typing import Any, Callable

import paramiko

from deployctl.providers.base import BaseProvider, RemoteFileInfo


class SFTPProvider(BaseProvider):
    """SFTP (SSH File Transfer Protocol) Provider."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ssh: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        if self.sftp:
            return

        port = self.port or 22
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": self.host,
            "port": port,
            "username": self.username,
            "timeout": self.timeout,
            "allow_agent": True,
            "look_for_keys": True,
        }

        if self.key_path:
            expanded_key = os.path.expanduser(self.key_path)
            if os.path.exists(expanded_key):
                connect_kwargs["key_filename"] = expanded_key

        if self.password:
            connect_kwargs["password"] = self.password

        ssh.connect(**connect_kwargs)
        self.ssh = ssh
        self.sftp = ssh.open_sftp()

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.connect()
            cwd = self.sftp.normalize(".") if self.sftp else ""
            return True, f"Successfully connected to SFTP {self.host}:{self.port or 22}. Remote CWD: {cwd}"
        except Exception as e:
            return False, f"SFTP connection error: {str(e)}"
        finally:
            self.close()

    def _full_remote_path(self, rel_path: str) -> str:
        clean_rel = rel_path.lstrip("/")
        if self.remote_path == "/" or not self.remote_path:
            return f"/{clean_rel}"
        return f"{self.remote_path}/{clean_rel}"

    def ensure_remote_dir(self, remote_rel_dir: str) -> None:
        if not self.sftp:
            self.connect()

        full_dir = self._full_remote_path(remote_rel_dir)
        parts = [p for p in full_dir.split("/") if p]
        current = ""

        for part in parts:
            current = f"{current}/{part}"
            try:
                self.sftp.stat(current)
            except IOError:
                try:
                    self.sftp.mkdir(current)
                except IOError:
                    pass

    def list_remote(self, sub_path: str = "") -> dict[str, RemoteFileInfo]:
        if not self.sftp:
            self.connect()

        results: dict[str, RemoteFileInfo] = {}
        base_dir = self._full_remote_path(sub_path)

        def _traverse(current_dir: str, rel_prefix: str) -> None:
            try:
                for entry in self.sftp.listdir_attr(current_dir):
                    name = entry.filename
                    if name in (".", ".."):
                        continue

                    item_rel = f"{rel_prefix}/{name}" if rel_prefix else name
                    item_remote = f"{current_dir}/{name}"

                    if stat.S_ISDIR(entry.st_mode):
                        _traverse(item_remote, item_rel)
                    else:
                        results[item_rel] = RemoteFileInfo(
                            rel_path=item_rel,
                            size=entry.st_size or 0,
                            mtime=float(entry.st_mtime) if entry.st_mtime else None,
                            is_dir=False,
                        )
            except Exception:
                pass

        _traverse(base_dir, "")
        return results

    def upload_file(
        self,
        local_path: Path,
        remote_rel_path: str,
        callback: Callable[[int], None] | None = None,
    ) -> bool:
        if not self.sftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        parent_dir = posixpath.dirname(full_remote)
        if parent_dir:
            self.ensure_remote_dir(posixpath.dirname(remote_rel_path))

        last_transferred = [0]

        def _cb(transferred: int, total: int):
            if callback:
                chunk = transferred - last_transferred[0]
                if chunk > 0:
                    callback(chunk)
                last_transferred[0] = transferred

        self.sftp.put(str(local_path), full_remote, callback=_cb)
        return True

    def download_file(self, remote_rel_path: str, local_path: Path) -> bool:
        if not self.sftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(full_remote, str(local_path))
        return True

    def delete_file(self, remote_rel_path: str) -> bool:
        if not self.sftp:
            self.connect()

        full_remote = self._full_remote_path(remote_rel_path)
        try:
            self.sftp.remove(full_remote)
            return True
        except Exception:
            return False

    def list_dir(self, remote_dir: str = "") -> list[dict[str, Any]]:
        """List contents of remote_dir via SFTP."""
        if not self.sftp:
            self.connect()

        target = remote_dir.strip() if remote_dir else "."
        try:
            entries = self.sftp.listdir_attr(target)
        except IOError:
            return []

        results: list[dict[str, Any]] = []
        for entry in entries:
            name = entry.filename
            if name in (".", ".."):
                continue
            is_dir = stat.S_ISDIR(entry.st_mode)
            full_path = posixpath.normpath(posixpath.join(target if target != "." else "/", name))
            if not full_path.startswith("/"):
                full_path = "/" + full_path
            results.append({
                "name": name,
                "path": full_path,
                "is_dir": is_dir,
                "size": entry.st_size or 0 if not is_dir else 0,
                "mtime": float(entry.st_mtime) if entry.st_mtime else None,
            })

        results.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return results

    def create_dir(self, remote_dir: str) -> bool:
        """Create remote directory structure recursively via SFTP."""
        if not self.sftp:
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
                self.sftp.stat(current)
            except IOError:
                try:
                    self.sftp.mkdir(current)
                except IOError:
                    pass
        return True

    def close(self) -> None:
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None

        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None
