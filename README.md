# deployctl 🚀

[![CI](https://github.com/donotwaitx/deployctl/actions/workflows/ci.yml/badge.svg)](https://github.com/donotwaitx/deployctl/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP Ready](https://img.shields.io/badge/MCP-Enabled-green.svg)](https://modelcontextprotocol.io)
[![M8ven Score](https://m8ven.ai/badge/mcp/donotwaitx-deployctl-bsb46z?v=91f043c4c0d00dc5c8047c80c8401dae)](https://m8ven.ai/mcp/donotwaitx-deployctl-bsb46z?s=readme)

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

### 5. Target safeguards (optional)

Extra keys on a target in `~/.deployctl/projects.yaml`:

```yaml
projects:
  my-webapp:
    production:
      allowed_branches: [main]        # refuse to deploy from any other branch (use --force to override)
      require_clean: true             # refuse to deploy with uncommitted changes in local_path
      post_deploy_delete:             # server files removed after a successful deploy (framework caches)
        - bootstrap/cache/*.php
      insecure_tls: false             # true only for self-signed hosts: skips certificate checks
      delete_missing: owned           # owned (default) | all | none - which server files a deploy may delete
```

* Every deployment records the git **branch, commit and dirty state**. Without `allowed_branches`, deploying from a branch other than `main` / `master` / `develop` only prints a warning, as does deploying from a different branch than the last deployment (files that exist only in the other branch can linger on the server).
* Files whose deletion fails are reported, kept in the state cache and retried by the next deployment; deletions never run when an upload failed.
* The state cache is ignored once it is older than `state_max_age_days` (default 7, in `config.yaml`) and the server is scanned again.
* **Server files take priority.** By default (`delete_missing: owned`) a deploy only deletes files deployctl itself deployed earlier and that are now gone locally. Files that exist only on the server (admin uploads, generated thumbnails) are never removed; they are listed as `skipped_deletes`. `all` restores the old "mirror the local tree" behaviour, `none` never deletes. Change it with `deployctl project set <project> <env> --delete-missing owned|all|none` or the MCP tool `set_target_option`.
* **Deleting server files always needs an explicit yes** (`--yes`, or `yes=true` over MCP); until then the run stops with `DELETIONS_NEED_CONFIRMATION` and the list of files.
* Only one deployment per `project:environment` runs at a time.
* `post_deploy_delete` patterns are relative to `remote_path`; only the file-name part may contain wildcards.

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
* `deploy_project`: Deploy projects with differential diffs and zip strategy. Never prompts: a production deploy first returns `CONFIRMATION_REQUIRED` with the diff, and runs when repeated with `yes=true`. Returns a JSON report (status, git branch/commit, warnings, file lists, failed uploads/deletes).
* `list_projects`: Inspect configured projects and targets.
* `test_connection`: Test server connectivity.
* `show_project`: View sanitized target settings.
* `browse_remote_directories`: Browse remote server folder hierarchy.
* `download_remote_file`: Download one remote file (e.g. a log) to `~/.deployctl/downloads/<project>/<env>/` and return its redacted tail.
* `set_target_option`: Change a validated target option (`delete_missing`, `allowed_branches`, `require_clean`, `insecure_tls`, `post_deploy_delete`, `app_url`, `zip_deploy`).
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
4. **TLS Verified by Default:** Requests to the PHP bridges verify the server certificate. Set `insecure_tls: true` on a target to skip verification; only then is the server-IP fallback (virtual host `Host` header) tried, since a certificate is never valid for a bare IP.
5. **Constant-time Token Check:** The PHP bridges compare tokens with `hash_equals`.

---

## ☕ Support

If deployctl saves you time, you can support its development:

- **MoMo:** [0336679423](https://nhantien.momo.vn/0336679423)
- **MB Bank:** `910191005`

<img src="https://img.vietqr.io/image/MB-910191005-compact2.png" alt="VietQR - MB Bank 910191005" width="240">

## 📄 License

MIT License © 2026 deployctl Contributors. See [LICENSE](LICENSE) for details.
