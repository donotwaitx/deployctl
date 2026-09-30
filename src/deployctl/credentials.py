"""Credential manager for deployctl.

Stores sensitive server credentials securely in macOS Keychain / Linux SecretService / Permission-locked Vault.
Agent only ever references the credential identifier (e.g. prod-server or my-app-prod).
Passwords remain isolated and only exist temporarily in process memory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import keyring

SERVICE_NAME = "deployctl"
INDEX_PATH = Path.home() / ".deployctl" / "credentials_index.json"
VAULT_PATH = Path.home() / ".deployctl" / ".credentials.vault"


def _ensure_index_dir() -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[str]:
    _ensure_index_dir()
    if not INDEX_PATH.exists():
        return []
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_index(names: list[str]) -> None:
    _ensure_index_dir()
    try:
        clean_names = sorted(list(set(names)))
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(clean_names, f, indent=2)
    except Exception:
        pass


def _vault_load() -> dict[str, str]:
    if not VAULT_PATH.exists():
        return {}
    try:
        with open(VAULT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _vault_save(data: dict[str, str]) -> bool:
    try:
        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Write with 0600 permissions (read/write only by owner)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(VAULT_PATH, flags, mode)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(VAULT_PATH, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _vault_set(name: str, raw_payload: str) -> bool:
    data = _vault_load()
    data[name] = raw_payload
    return _vault_save(data)


def _vault_get(name: str) -> str | None:
    data = _vault_load()
    return data.get(name)


def _vault_delete(name: str) -> bool:
    data = _vault_load()
    if name in data:
        del data[name]
        return _vault_save(data)
    return True


def _macos_security_set(service: str, account: str, secret: str) -> bool:
    """Fallback to macOS native security CLI to add/update generic password."""
    try:
        cmd = [
            "/usr/bin/security",
            "add-generic-password",
            "-a", account,
            "-s", service,
            "-w", secret,
            "-U",  # Update if exists
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0
    except Exception:
        return False


def _macos_security_get(service: str, account: str) -> str | None:
    """Fallback to macOS native security CLI to retrieve generic password."""
    try:
        cmd = [
            "/usr/bin/security",
            "find-generic-password",
            "-a", account,
            "-s", service,
            "-w",  # Output password only
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def _macos_security_delete(service: str, account: str) -> bool:
    """Fallback to macOS native security CLI to delete generic password."""
    try:
        cmd = [
            "/usr/bin/security",
            "delete-generic-password",
            "-a", account,
            "-s", service,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0
    except Exception:
        return False


def save_credential(
    name: str,
    host: str,
    username: str,
    password: str,
    port: int | None = None,
    key_path: str | None = None,
    protocol: str | None = None,
) -> bool:
    """Save server credentials into macOS Keychain or Linux Keyring/Vault.
    
    Data is stored as a JSON string under service 'deployctl' and account <name>.
    """
    data = {
        "host": host.strip(),
        "username": username.strip(),
        "password": password,
        "port": port,
        "key_path": key_path.strip() if key_path else None,
        "protocol": protocol.strip() if protocol else None,
    }
    raw_payload = json.dumps(data)

    success = False
    try:
        keyring.set_password(SERVICE_NAME, name, raw_payload)
        success = True
    except Exception:
        if sys.platform == "darwin":
            success = _macos_security_set(SERVICE_NAME, name, raw_payload)
        else:
            success = _vault_set(name, raw_payload)

    if not success:
        # Final fallback for headless environments
        success = _vault_set(name, raw_payload)

    if success:
        names = _load_index()
        if name not in names:
            names.append(name)
            _save_index(names)

    return success


def get_credential(name: str) -> dict[str, Any] | None:
    """Retrieve credential payload from Keychain or Linux Keyring/Vault.
    
    Returns a dictionary with host, username, password, port, key_path.
    """
    raw_payload: str | None = None
    try:
        raw_payload = keyring.get_password(SERVICE_NAME, name)
    except Exception:
        pass

    if raw_payload is None and sys.platform == "darwin":
        raw_payload = _macos_security_get(SERVICE_NAME, name)

    if raw_payload is None:
        raw_payload = _vault_get(name)

    if not raw_payload:
        return None

    try:
        parsed = json.loads(raw_payload)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        # Fallback if raw password was stored
        return {
            "host": "",
            "username": "",
            "password": raw_payload,
            "port": None,
            "key_path": None,
        }

    return None


def delete_credential(name: str) -> bool:
    """Delete a credential from Keychain / Vault and local index."""
    success = False
    try:
        keyring.delete_password(SERVICE_NAME, name)
        success = True
    except Exception:
        if sys.platform == "darwin":
            success = _macos_security_delete(SERVICE_NAME, name)

    _vault_delete(name)

    names = _load_index()
    if name in names:
        names.remove(name)
        _save_index(names)
        success = True

    return success


def list_credentials() -> list[str]:
    """Return all configured credential names.
    
    Never exposes passwords.
    """
    return _load_index()
