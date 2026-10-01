"""Configuration management for deployctl.

Loads ~/.deployctl/config.yaml and ~/.deployctl/projects.yaml.
Supports project-level overrides (.deployctl.yaml).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEPLOYCTL_HOME = Path.home() / ".deployctl"
GLOBAL_CONFIG_FILE = DEPLOYCTL_HOME / "config.yaml"
PROJECTS_FILE = DEPLOYCTL_HOME / "projects.yaml"
LOGS_DIR = DEPLOYCTL_HOME / "logs"
DOWNLOADS_DIR = DEPLOYCTL_HOME / "downloads"

DEFAULT_EXCLUDES = [
    ".git*",
    ".DS_Store",
    ".env*",
    "node_modules/**",
    "__pycache__/**",
    "*.pyc",
    ".venv/**",
    "venv/**",
    "*.log",
    "deployctl.yaml",
    ".deployctl.yaml",
]

DEFAULT_CONFIG = {
    "default_environment": "production",
    "enforce_project_isolation": False,
    "confirm_production": True,
    "log_retention_days": 30,
    # A state cache older than this is ignored and the remote is scanned again (0 = never expires).
    "state_max_age_days": 7,
    "default_excludes": DEFAULT_EXCLUDES,
}


def ensure_config_dirs() -> None:
    """Ensure ~/.deployctl and ~/.deployctl/logs exist."""
    DEPLOYCTL_HOME.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def load_global_config() -> dict[str, Any]:
    """Load global config from ~/.deployctl/config.yaml."""
    ensure_config_dirs()
    if not GLOBAL_CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(GLOBAL_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            cfg = DEFAULT_CONFIG.copy()
            cfg.update(data)
            return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()


def load_projects(custom_path: Path | None = None) -> dict[str, Any]:
    """Load projects registry from ~/.deployctl/projects.yaml or local override."""
    ensure_config_dirs()

    # Check for local directory project override
    local_override = Path.cwd() / ".deployctl.yaml"
    if not local_override.exists():
        local_override = Path.cwd() / "deployctl.yaml"

    target_file = custom_path or (local_override if local_override.exists() else PROJECTS_FILE)

    if not target_file.exists():
        return {"projects": {}}

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "projects" not in data:
                data = {"projects": data}
            return data
    except Exception:
        return {"projects": {}}


def save_projects(data: dict[str, Any], target_file: Path | None = None) -> None:
    """Save projects registry to ~/.deployctl/projects.yaml."""
    ensure_config_dirs()
    out_file = target_file or PROJECTS_FILE
    with open(out_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, sort_keys=False, default_flow_style=False)


def get_project_target(project_name: str, env_name: str = "production") -> dict[str, Any]:
    """Retrieve specific environment config for a project."""
    projects_data = load_projects()
    projects = projects_data.get("projects", {})

    # Case-insensitive lookup
    target_project = None
    target_name = project_name
    for p_name, p_val in projects.items():
        if p_name.lower() == project_name.lower():
            target_project = p_val
            target_name = p_name
            break

    if not target_project:
        raise ValueError(
            f"Project '{project_name}' not found in registry ({PROJECTS_FILE}). "
            f"Available projects: {', '.join(projects.keys()) or 'None'}"
        )

    # Lookup environment
    target_env = None
    actual_env = env_name
    for e_name, e_val in target_project.items():
        if e_name.lower() == env_name.lower():
            target_env = e_val
            actual_env = e_name
            break

    # Alias fallback: prod <-> production, stage <-> staging, dev <-> development
    if not target_env:
        alias_map = {
            "prod": "production",
            "production": "prod",
            "dev": "development",
            "development": "dev",
            "stage": "staging",
            "staging": "stage",
        }
        alt_name = alias_map.get(env_name.lower())
        if alt_name:
            for e_name, e_val in target_project.items():
                if e_name.lower() == alt_name:
                    target_env = e_val
                    actual_env = e_name
                    break

    if not target_env:
        raise ValueError(
            f"Environment '{env_name}' not found for project '{target_name}'. "
            f"Available environments: {', '.join(target_project.keys())}"
        )

    env_cfg = target_env.copy()
    env_cfg["project"] = target_name
    env_cfg["environment"] = actual_env

    # Merge default excludes
    global_cfg = load_global_config()
    combined_excludes = list(global_cfg.get("default_excludes", DEFAULT_EXCLUDES))
    if "exclude" in env_cfg and isinstance(env_cfg["exclude"], list):
        combined_excludes.extend(env_cfg["exclude"])
    env_cfg["exclude"] = list(set(combined_excludes))

    return env_cfg


def init_sample_config() -> tuple[Path, Path]:
    """Initialize sample config.yaml and projects.yaml if not present."""
    ensure_config_dirs()

    if not GLOBAL_CONFIG_FILE.exists():
        with open(GLOBAL_CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_CONFIG, f, sort_keys=False)

    if not PROJECTS_FILE.exists():
        sample_projects = {
            "projects": {
                "my-webapp": {
                    "production": {
                        "protocol": "ftp",
                        "credential": "my-webapp-prod",
                        "remote_path": "/public_html",
                        "local_path": ".",
                        "zip_deploy": True,
                        "app_url": "https://example.com",
                    },
                    "staging": {
                        "protocol": "ftp",
                        "credential": "my-webapp-stage",
                        "remote_path": "/public_html/staging",
                        "local_path": ".",
                    },
                },
                "api-service": {
                    "production": {
                        "protocol": "sftp",
                        "credential": "api-service-prod",
                        "remote_path": "/var/www/api",
                        "local_path": ".",
                    }
                },
            }
        }
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            yaml.dump(sample_projects, f, sort_keys=False, default_flow_style=False)

    return GLOBAL_CONFIG_FILE, PROJECTS_FILE

