# deployctl Agent Skill Package

Official portable Agent Skill for **deployctl** — the secure, ultra-fast differential deployment CLI & Model Context Protocol (MCP) server for AI agents.

This directory provides universal instructions that teach AI coding assistants (Claude Code, Cursor, GitHub Copilot, Google Antigravity, OpenHands, Codex CLI, etc.) how to safely inspect, preview, and deploy web applications to FTP, FTPS, and SFTP hosting servers.

---

## 📂 Contents

- [`SKILL.md`](./SKILL.md): Standard Agent Skill definition with YAML frontmatter, execution workflows, safety gates, and error recovery protocols.

---

## 📥 Installation

### 1. Using deployctl CLI (Recommended)
```bash
# Auto-install to all detected agents (Claude Code, Cursor, Antigravity)
deployctl skill install

# Or install for a specific agent
deployctl skill install --agent claude
deployctl skill install --agent cursor
```

### 2. Using NPX (Node Ecosystem)
```bash
npx deployctl-skill
# or target a specific agent
npx deployctl-skill --agent claude
```

### 3. Manual Copy
- **Claude Code:** Copy `SKILL.md` to `~/.claude/skills/deployctl/SKILL.md` (global) or `.claude/skills/deployctl/SKILL.md` (project).
- **Cursor:** Copy `SKILL.md` to `~/.cursor/rules/deployctl.md` or `.cursor/rules/deployctl.md`.
- **Antigravity:** Add `deployctl mcp` to `~/.gemini/antigravity/mcp_config.json`.

---

## 📦 Marketplace Export

To build a clean ZIP file for listing on AI skill marketplaces (such as Agensi, Glama, or Smithery):
```bash
deployctl skill package
```
This generates `dist/deployctl-skill.zip`.

---

## 🛡️ Safety Guarantees

- **No Secret Prompting:** The skill forbids agents from asking users for passwords or private keys in chat prompts. All credentials remain in the system Keychain/SecretService.
- **Mandatory Dry-Run First:** All deployments preview differential file diffs before executing live changes.
- **Explicit Confirmation Gates:** Production deployments and server-side file deletions require explicit user confirmation.
