"""Skill installer and packager module for deployctl."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Literal

SUPPORTED_AGENTS = ("claude", "cursor", "antigravity", "all")
AgentType = Literal["claude", "cursor", "antigravity", "all"]


def get_canonical_skill_path() -> Path:
    """Return the path to the canonical SKILL.md in the repo or installed package."""
    # 1. Look in repo root if running from source checkout
    repo_root = Path(__file__).resolve().parent.parent.parent
    local_skill = repo_root / "skills" / "deployctl" / "SKILL.md"
    if local_skill.exists():
        return local_skill

    # 2. Look in packaged data
    pkg_skill = Path(__file__).resolve().parent / "skills" / "deployctl" / "SKILL.md"
    if pkg_skill.exists():
        return pkg_skill

    # Fallback to local_skill path
    return local_skill


def get_skill_content() -> str:
    """Read canonical skill content."""
    skill_file = get_canonical_skill_path()
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")
    raise FileNotFoundError(f"deployctl SKILL.md not found at {skill_file}")


def install_skill_for_claude(project_dir: Path | None = None, is_global: bool = True) -> list[Path]:
    """Install skill for Claude Code."""
    content = get_skill_content()
    destinations: list[Path] = []

    if is_global:
        dest_dir = Path.home() / ".claude" / "skills" / "deployctl"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_file = dest_dir / "SKILL.md"
        target_file.write_text(content, encoding="utf-8")
        destinations.append(target_file)

    if project_dir or not is_global:
        base = project_dir or Path.cwd()
        proj_dir = base / ".claude" / "skills" / "deployctl"
        proj_dir.mkdir(parents=True, exist_ok=True)
        target_file = proj_dir / "SKILL.md"
        target_file.write_text(content, encoding="utf-8")
        destinations.append(target_file)

    return destinations


def install_skill_for_cursor(project_dir: Path | None = None, is_global: bool = True) -> list[Path]:
    """Install skill as Cursor rule."""
    content = get_skill_content()
    destinations: list[Path] = []

    if is_global:
        dest_dir = Path.home() / ".cursor" / "rules"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target_file = dest_dir / "deployctl.md"
        target_file.write_text(content, encoding="utf-8")
        destinations.append(target_file)

    if project_dir or not is_global:
        base = project_dir or Path.cwd()
        proj_dir = base / ".cursor" / "rules"
        proj_dir.mkdir(parents=True, exist_ok=True)
        target_file = proj_dir / "deployctl.md"
        target_file.write_text(content, encoding="utf-8")
        destinations.append(target_file)

    return destinations


def install_skill_for_antigravity() -> list[Path]:
    """Ensure Antigravity MCP config includes deployctl."""
    destinations: list[Path] = []
    config_paths = [
        Path.home() / ".gemini" / "antigravity" / "mcp_config.json",
        Path.home() / ".gemini" / "config" / "mcp_config.json",
    ]

    for cfg_file in config_paths:
        if cfg_file.parent.exists():
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            data: dict = {}
            if cfg_file.exists():
                try:
                    data = json.loads(cfg_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}

            if "mcpServers" not in data:
                data["mcpServers"] = {}

            data["mcpServers"]["deployctl"] = {
                "command": "deployctl",
                "args": ["mcp"],
            }
            cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            destinations.append(cfg_file)

    return destinations


def package_skill_zip(output_dir: Path | None = None) -> Path:
    """Package skills/deployctl into a marketplace-ready ZIP archive."""
    skill_file = get_canonical_skill_path()
    if not skill_file.exists():
        raise FileNotFoundError(f"Cannot package: {skill_file} not found")

    out_base = output_dir or Path.cwd() / "dist"
    out_base.mkdir(parents=True, exist_ok=True)
    zip_path = out_base / "deployctl-skill.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(skill_file, arcname="deployctl/SKILL.md")
        readme_file = skill_file.parent / "README.md"
        if readme_file.exists():
            zf.write(readme_file, arcname="deployctl/README.md")

    return zip_path
