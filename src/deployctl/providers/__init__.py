"""Provider registry and factory for deployctl."""

from __future__ import annotations

from typing import Any

from deployctl.providers.base import BaseProvider, RemoteFileInfo
from deployctl.providers.ftp import FTPProvider
from deployctl.providers.ftps import FTPSProvider
from deployctl.providers.local import LocalProvider
from deployctl.providers.sftp import SFTPProvider

PROVIDERS: dict[str, type[BaseProvider]] = {
    "ftp": FTPProvider,
    "ftps": FTPSProvider,
    "sftp": SFTPProvider,
    "local": LocalProvider,
}


def get_provider(
    protocol: str,
    host: str = "",
    username: str = "",
    password: str | None = None,
    port: int | None = None,
    key_path: str | None = None,
    remote_path: str = "/",
    **kwargs: Any,
) -> BaseProvider:
    """Instantiate and return the requested deployment provider."""
    proto_key = protocol.lower().strip()
    if proto_key not in PROVIDERS:
        raise ValueError(
            f"Unsupported protocol '{protocol}'. Supported: {', '.join(PROVIDERS.keys())}"
        )

    provider_cls = PROVIDERS[proto_key]
    return provider_cls(
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path=remote_path,
        **kwargs,
    )
