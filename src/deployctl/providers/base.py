"""Base provider interface for deployment protocols.

Supports FTP, FTPS, SFTP, and Local filesystem.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class RemoteFileInfo:
    rel_path: str
    size: int
    mtime: float | None = None
    is_dir: bool = False


class BaseProvider(ABC):
    """Abstract deployment provider."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str | None = None,
        port: int | None = None,
        key_path: str | None = None,
        remote_path: str = "/",
        timeout: int = 30,
        **kwargs,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.key_path = key_path
        self.remote_path = remote_path.rstrip("/") if remote_path != "/" else "/"
        self.timeout = timeout
        self.extra = kwargs

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to remote server."""
        pass

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Test connection and return (success, message)."""
        pass

    @abstractmethod
    def list_remote(self, sub_path: str = "") -> dict[str, RemoteFileInfo]:
        """List files recursively under remote base path."""
        pass

    @abstractmethod
    def upload_file(
        self,
        local_path: Path,
        remote_rel_path: str,
        callback: Callable[[int], None] | None = None,
    ) -> bool:
        """Upload a single file to remote destination."""
        pass

    @abstractmethod
    def download_file(self, remote_rel_path: str, local_path: Path) -> bool:
        """Download a single file from remote."""
        pass

    @abstractmethod
    def delete_file(self, remote_rel_path: str) -> bool:
        """Delete remote file."""
        pass

    @abstractmethod
    def ensure_remote_dir(self, remote_rel_dir: str) -> None:
        """Ensure parent directory exists remotely."""
        pass

    @abstractmethod
    def list_dir(self, remote_dir: str = "") -> list[dict[str, Any]]:
        """List immediate files and subdirectories inside remote_dir."""
        pass

    @abstractmethod
    def create_dir(self, remote_dir: str) -> bool:
        """Create remote directory structure."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close connection cleanly."""
        pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
