"""Security utilities for deployctl.

Ensures credentials never leak into terminal logs, exception traces,
or process arguments.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def mask_secret(secret: str | None, show_chars: int = 0) -> str:
    """Mask a secret string (e.g. password, API token).
    
    If show_chars > 0, keeps that many characters visible at the end,
    otherwise masks completely.
    """
    if not secret:
        return "********"
    if show_chars <= 0 or len(secret) <= show_chars:
        return "********"
    return "*" * (len(secret) - show_chars) + secret[-show_chars:]


def sanitize_text(text: str, secrets: list[str] | None = None) -> str:
    """Sanitize text by replacing sensitive secrets and credentials with [REDACTED].
    
    Also sanitizes common URL embedded credentials like ftp://user:pass@host.
    """
    if not text:
        return text

    # Redact known secrets
    if secrets:
        for s in secrets:
            if s and len(s) > 2:
                text = text.replace(s, "[REDACTED]")

    # Redact url auth patterns like ftp://user:password@host or https://user:password@host
    url_pattern = re.compile(r"([a-zA-Z]+://[^:]+:)([^@]+)(@)")
    text = url_pattern.sub(r"\1[REDACTED]\3", text)

    # Redact password parameter flags like --password secret or -p secret
    pwd_pattern = re.compile(r"(--password(?:=|\s+)|-p\s+)(\S+)", re.IGNORECASE)
    text = pwd_pattern.sub(r"\1[REDACTED]", text)

    return text


def check_project_isolation(
    project_name: str,
    project_config: dict,
    current_cwd: Path | None = None,
) -> tuple[bool, str]:
    """Check Tier 2 Project Isolation.
    
    Verifies that the current working directory matches the project being deployed
    if isolation enforcement is enabled.
    """
    cwd = (current_cwd or Path.cwd()).resolve()
    local_path_str = project_config.get("local_path", ".")
    configured_local = Path(local_path_str).expanduser()
    
    # Check if cwd is within configured local path or project folder name matches
    cwd_name = cwd.name
    
    if cwd_name.lower() == project_name.lower():
        return True, "CWD matches project name"
        
    try:
        if configured_local.is_absolute():
            if cwd == configured_local or configured_local in cwd.parents or cwd in configured_local.parents:
                return True, "CWD matches configured local_path"
    except Exception:
        pass
        
    return False, f"Current directory ({cwd_name}) does not match target project ({project_name})"
