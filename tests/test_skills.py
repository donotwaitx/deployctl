"""Tests for the official deployctl Agent Skill documentation and metadata."""

from pathlib import Path
import yaml
from deployctl.mcp import MCP_TOOLS


def test_skill_file_exists_and_has_valid_frontmatter():
    skill_path = Path(__file__).parent.parent / "skills" / "deployctl" / "SKILL.md"
    assert skill_path.exists(), "skills/deployctl/SKILL.md does not exist"

    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---"), "SKILL.md must begin with YAML frontmatter"

    parts = content.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have valid YAML frontmatter delimiters"

    meta = yaml.safe_load(parts[1])
    assert meta.get("name") == "deployctl"
    assert "description" in meta and len(meta["description"]) > 20
    assert "tools" in meta and isinstance(meta["tools"], list)

    # Verify all 8 registered MCP tools are declared in the skill frontmatter
    expected_tools = {t["name"] for t in MCP_TOOLS}
    declared_tools = set(meta["tools"])
    assert expected_tools == declared_tools, f"Tool mismatch. Expected: {expected_tools}, Declared: {declared_tools}"


def test_skill_security_rules_present():
    skill_path = Path(__file__).parent.parent / "skills" / "deployctl" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    # Verify key safety principles are documented
    assert "Zero Credential Exposure" in content
    assert "Dry-Run" in content
    assert "Explicit Confirmation Gate" in content
    assert "delete_missing: owned" in content
    assert "allowed_branches" in content
