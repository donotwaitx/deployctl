"""Tests for skill installer functions and CLI commands."""

import tempfile
import zipfile
from pathlib import Path
from typer.testing import CliRunner

from deployctl.cli import app
from deployctl.skill_installer import (
    get_canonical_skill_path,
    get_skill_content,
    install_skill_for_claude,
    install_skill_for_cursor,
    package_skill_zip,
)

runner = CliRunner()


def test_canonical_skill_path_resolves():
    path = get_canonical_skill_path()
    assert path.name == "SKILL.md"
    assert path.exists()


def test_get_skill_content_has_tools():
    content = get_skill_content()
    assert "deploy_project" in content
    assert "Zero Credential Exposure" in content


def test_install_skill_for_claude_project(tmp_path, monkeypatch):
    installed = install_skill_for_claude(project_dir=tmp_path, is_global=False)
    assert len(installed) == 1
    target = tmp_path / ".claude" / "skills" / "deployctl" / "SKILL.md"
    assert target.exists()
    assert "deploy_project" in target.read_text(encoding="utf-8")


def test_install_skill_for_cursor_project(tmp_path, monkeypatch):
    installed = install_skill_for_cursor(project_dir=tmp_path, is_global=False)
    assert len(installed) == 1
    target = tmp_path / ".cursor" / "rules" / "deployctl.md"
    assert target.exists()
    assert "deployctl" in target.read_text(encoding="utf-8")


def test_package_skill_zip(tmp_path):
    zip_file = package_skill_zip(output_dir=tmp_path)
    assert zip_file.exists()
    assert zip_file.name == "deployctl-skill.zip"

    with zipfile.ZipFile(zip_file, "r") as zf:
        names = zf.namelist()
        assert "deployctl/SKILL.md" in names
        assert "deployctl/README.md" in names


def test_cli_skill_commands(tmp_path):
    # Test skill path
    result = runner.invoke(app, ["skill", "path"])
    assert result.exit_code == 0
    assert "SKILL.md" in result.stdout

    # Test skill package
    result = runner.invoke(app, ["skill", "package", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "deployctl-skill.zip").exists()
