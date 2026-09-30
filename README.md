# deployctl 🚀

[![CI](https://github.com/donotwaitx/deployctl/actions/workflows/ci.yml/badge.svg)](https://github.com/donotwaitx/deployctl/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP Ready](https://img.shields.io/badge/MCP-Enabled-green.svg)](https://modelcontextprotocol.io)

> **Secure, Ultra-Fast Differential Deployment CLI & Model Context Protocol (MCP) Server for AI Agents (Antigravity, Claude Code) and Developers.**

Deploy large web applications and Composer `vendor/` folders over FTP, FTPS, and SFTP in **seconds instead of hours** without exposing server credentials to AI agents or CI/CD logs.

---

## ⚡ Why deployctl?

* **⚡ Ultra-Fast Zip & Self-Destructing PHP Auto-Extract Bridge:** Compresses differential changes into a temporary zip package, uploads in one stream, and extracts directly on remote hosting in **<1 second** (turning 45-minute sequential uploads into 30 seconds).
* **🔍 Fast Remote Manifest Scanner Bridge:** Queries remote server state in **0.35 seconds** over a single HTTP request using recursive server-side iteration instead of slow sequential FTP roundtrips.
* **🛡️ Zero-Leak Credential Isolation:** Passwords and SSH keys are stored in native **macOS Keychain**, **Linux SecretService**, or permission-locked headless vaults (`chmod 0600`). Agents and logs never see or expose raw secrets.
* **🤖 Model Context Protocol (MCP) Server:** Native stdio JSON-RPC 2.0 tools for **Google Antigravity** and **Claude Code** to perform deployments, remote explorations, and dry-run diffs autonomously and safely.
* **🎯 Change Detection Engine:** Computes differential file diffs (Added, Modified, Deleted, Unchanged) locally or against remote state caches in ~0.02s.
* **📂 Interactive Remote Directory Explorer:** Browse and select target deployment directories directly from the terminal or MCP before deploying.
* **🌐 Headless & CI/CD Ready:** Works seamlessly inside GitHub Actions, GitLab CI, and Docker containers with automated confirmations (`--yes`) and secret masking.

---

## 🏛 Architecture

```
                    ┌───────────────────────────┐
                    │  Antigravity / Claude Code │
                    └─────────────┬─────────────┘
                                  │ (MCP stdio JSON-RPC)
                    ┌─────────────▼─────────────┐
                    │         deployctl         │
                    └─────────────┬─────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
  Local State Cache        Encrypted Vault         Fast Deployment
  ~/.deployctl/state/      macOS Keychain / Linux  Differential Engine
         │                        │                        │
         └────────────────────────┴────────┬───────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
                Direct FTP / SFTP                     Zip & PHP Bridge
              (Sequential sync mode)               (High-speed mode <1s)
                        │                                     │
                        └──────────────────┬──────────────────┘
                                           ▼
                                100+ Remote Hostings
```

> **The Core Boundary:** AI Agent knows the `project`. CLI knows the `server`. Keychain/Vault holds the `secret`.

---

## 📦 Installation

Install globally using [uv](https://github.com/astral-sh/uv) (recommended) or pip:

```bash
# Using uv (fastest)
uv tool install --editable . --force

# Or via standard pip
pip install -e .
```

Verify installation:
```bash
deployctl --help
```

---

## 🚀 Quick Start

### 1. Initialize Configuration
```bash
deployctl init
```
This generates default configuration files in `~/.deployctl/`:
* `config.yaml`: Global preferences and default exclude patterns.
* `projects.yaml`: Project deployment targets registry.

### 2. Add Server Credentials
Securely store your server credentials into the system Keychain/Vault:
```bash
deployctl credential add my-webapp-prod \
  --host "ftp.example.com" \
  --username "deployer" \
  --protocol ftp
# (Prompts for password securely without terminal echo)
```

### 3. Configure a Deployment Target
Link a project environment to the stored credentials:
```bash
deployctl project set my-webapp production \
  --credential my-webapp-prod \
  --remote-path "/public_html" \
  --zip \
  --app-url "https://example.com"
```

### 4. Deploy!
```bash
# Preview changes with dry-run diff
deployctl deploy my-webapp production --dry-run

# Deploy with automatic confirmation
deployctl deploy my-webapp production --yes
```

---

## 📖 CLI Reference

### 🚀 Deployments
| Command | Description |
|---|---|
| `deployctl deploy <project> [env]` | Deploy changes to target server |
| `deployctl deploy <project> --dry-run` | Inspect differential changes without uploading |
| `deployctl deploy <project> --remote-scan` | Force direct live remote scan via PHP bridge |
| `deployctl deploy <project> --yes` | Bypass production confirmation prompt |

### 🔑 Credentials Management
| Command | Description |
|---|---|
| `deployctl credential add <name>` | Save server credentials securely |
| `deployctl credential list` | List stored credential identifiers |
| `deployctl credential test <name>` | Test connectivity with stored credentials |
| `deployctl credential delete <name>` | Delete credentials from Keychain/Vault |

### 📂 Remote Exploration
| Command | Description |
|---|---|
| `deployctl remote ls <project> [path]` | List files and directories on remote hosting |
| `deployctl remote select <project>` | Interactively browse remote folders and save `remote_path` |
| `deployctl remote mkdir <project> <path>` | Create remote directory on hosting server |
| `deployctl remote scan <project>` | Fast-sync remote manifest into local state cache |

### 📋 Audit & Configuration
| Command | Description |
|---|---|
| `deployctl list` | View all configured projects, targets, and status |
| `deployctl show <project> [env]` | Display sanitized target configuration |
| `deployctl history [project]` | Show past deployment audit logs |
| `deployctl test <project> [env]` | Test target connectivity |
| `deployctl mcp` | Launch MCP server on stdio |

---

## 🤖 Model Context Protocol (MCP) Integration

`deployctl` comes with a built-in MCP server for AI Coding Agents.

### Antigravity Configuration
Add to `~/.gemini/antigravity/mcp_config.json` or `~/.gemini/config/mcp_config.json`:

```json
{
  "mcpServers": {
    "deployctl": {
      "command": "deployctl",
      "args": ["mcp"]
    }
  }
}
```

### Claude Code Configuration
Add to `~/.claude.json` or project settings:

```json
{
  "mcpServers": {
    "deployctl": {
      "command": "deployctl",
      "args": ["mcp"]
    }
  }
}
```

### Available MCP Tools:
* `deploy_project`: Deploy projects with differential diffs and zip strategy.
* `list_projects`: Inspect configured projects and targets.
* `test_connection`: Test server connectivity.
* `show_project`: View sanitized target settings.
* `browse_remote_directories`: Browse remote server folder hierarchy.
* `set_remote_path`: Configure remote destination folder.

---

## 🔄 GitHub Actions CI/CD Integration

### Option A: Using the Official GitHub Action (Recommended)

Simply use `donotwaitx/deployctl@main` in your workflow:

```yaml
name: Deploy to Hosting

on:
  push:
    branches: [ main ]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: 📥 Checkout Code
        uses: actions/checkout@v4

      - name: 🚀 Ultra-Fast Deploy
        uses: donotwaitx/deployctl@main
        with:
          host: ${{ secrets.FTP_HOST }}
          username: ${{ secrets.FTP_USERNAME }}
          password: ${{ secrets.FTP_PASSWORD }}
          protocol: ftp
          remote_path: "/public_html"
          app_url: "https://example.com"
          zip_deploy: "true"
          remote_scan: "true"
```

### Option B: Using CLI directly via `uv`

```yaml
      - name: 🐍 Set up uv
        uses: astral-sh/setup-uv@v3

      - name: 🚀 Deploy via deployctl CLI
        env:
          FTP_HOST: ${{ secrets.FTP_HOST }}
          FTP_USER: ${{ secrets.FTP_USER }}
          FTP_PASS: ${{ secrets.FTP_PASSWORD }}
        run: |
          uv tool install deployctl
          deployctl credential add prod-cred --host "$FTP_HOST" --username "$FTP_USER" --password "$FTP_PASS"
          deployctl project set my-app production --credential prod-cred --remote-path "/public_html" --zip --app-url "https://example.com"
          deployctl deploy my-app production --yes --remote-scan
```

---

## 🛡️ Security & Privacy Guarantees

1. **Passwords Never Logged:** Passwords, API tokens, and SSH keys are automatically masked (`[REDACTED]`) in all logs, error traces, and MCP responses.
2. **Self-Destructing PHP Bridges:** Temporary PHP worker scripts execute in memory, enforce 32-hex random authentication tokens, and self-delete immediately upon execution (`register_shutdown_function` + `@unlink`).
3. **Project Isolation Safeguards:** Prevents accidental cross-deployments when working in different repository working trees.
4. **Smart DNS Bypass:** Automatically supports IP targeting with virtual host `Host` headers when custom domains are not yet pointed to DNS.

---

## 📄 License

MIT License © 2026 deployctl Contributors. See [LICENSE](LICENSE) for details.
