---
name: deployctl
description: Safely inspect, preview diffs, and deploy web applications to FTP, FTPS, and SFTP hosting servers using deployctl MCP tools or CLI. Use whenever deploying code, inspecting remote files, checking remote error logs, or configuring deployment targets.
version: 1.0.0
tags:
  - deployment
  - ftp
  - ftps
  - sftp
  - mcp
  - devops
tools:
  - deploy_project
  - list_projects
  - test_connection
  - show_project
  - browse_remote_directories
  - download_remote_file
  - set_target_option
  - set_remote_path
---

# deployctl Agent Skill

This skill guides AI agents (Claude Code, Cursor, Copilot, Antigravity, and other coding assistants) to deploy web applications safely and efficiently using `deployctl`.

---

## 🛡️ Core Security & Safety Rules

1. **Zero Credential Exposure in Chat Prompts:**
   - **NEVER** ask users to paste passwords, FTP credentials, or SSH private keys into chat.
   - Credentials are stored securely in the system Keychain/SecretService vault.
   - If credentials are missing, instruct the user to run in their local terminal:
     ```bash
     deployctl credential add <credential-name> --host <host> --username <user> --protocol ftp
     ```

2. **Mandatory 5-Step Phasing:**
   - **Phase 1: Discovery** → Inspect configured targets and test reachability.
   - **Phase 2: Safety Check** → Verify git branch and clean working tree status.
   - **Phase 3: Dry-Run Diff** → Compute differential changes without uploading.
   - **Phase 4: Explicit Confirmation Gate** → Present diff summary and ask approval.
   - **Phase 5: Execute & Verify** → Deploy with `yes=true` and verify logs.

3. **Protection of Server-Side Files (`delete_missing: owned`):**
   - By default, `deployctl` only deletes files it previously deployed. Server-only files (user uploads, CMS media) are preserved.
   - Never advise users to enable `delete_missing: all` unless they explicitly want a full destructive mirror.

4. **Git Policy Constraints:**
   - Do not pass `force_branch=true` or `--force` unless the user explicitly asks to override branch policy or deploy dirty working tree changes.

---

## 🔄 Workflow Execution Guide

### Phase 1: Discovery & Target Inspection

1. Call `list_projects` to view available targets and credential status:
   ```json
   {}
   ```
2. Call `show_project` to inspect target settings (`allowed_branches`, `require_clean`, `delete_missing`, `remote_path`):
   ```json
   {
     "project": "my-webapp",
     "environment": "production"
   }
   ```
3. Call `test_connection` to confirm remote server connectivity:
   ```json
   {
     "project": "my-webapp",
     "environment": "production"
   }
   ```

---

### Phase 2: Git Policy & Pre-Flight Check

Check current local git repository state before proceeding:
- Verify current branch matches target's `allowed_branches` (if set).
- If `require_clean: true` is enabled on the target, ensure there are no uncommitted changes in `local_path`.
- If blocked by git policy, explain the issue clearly to the user instead of silently bypassing.

---

### Phase 3: Dry-Run Diff Calculation

Always run a dry-run first to preview changes:
```json
{
  "project": "my-webapp",
  "environment": "production",
  "dry_run": true
}
```

Parse the returned `DeployReport` JSON:
- `files.added`: list of new files to upload
- `files.modified`: list of updated files
- `files.deleted`: list of files queued for deletion
- `skipped_deletes`: server-only files protected from deletion
- `counts`: total added, modified, deleted, and byte size
- `warnings`: branch mismatch or stale cache notices

Present a concise summary to the user:
```markdown
### 📋 Deployment Preview (my-webapp -> production)
- **Status:** DRY_RUN
- **Git:** main (commit 4cb9c95, clean)
- **Added:** 3 files
- **Modified:** 2 files
- **Deleted:** 0 files (12 server-only files preserved)
- **Payload Size:** 142.5 KB
```

---

### Phase 4: Explicit Confirmation Gate

For **production** environments or whenever deletions are pending:
- Running `deploy_project(..., yes=false)` returns `CONFIRMATION_REQUIRED` or `DELETIONS_NEED_CONFIRMATION`.
- **Stop and ask the user for confirmation in the chat.**
- Do **not** proceed to Phase 5 until the user explicitly confirms (e.g., "Yes, proceed with deployment").

---

### Phase 5: Deployment Execution & Verification

1. Once confirmed, execute deployment:
   ```json
   {
     "project": "my-webapp",
     "environment": "production",
     "yes": true
   }
   ```

2. Evaluate the deployment result:
   - `status: "SUCCESS"` / `"SUCCESS_ZIP"`: Report success, files uploaded, and duration.
   - `status: "UP_TO_DATE"`: Notify user that server is already up to date.
   - `status: "LOCKED"`: Inform user another deployment is currently running on this target.
   - `status: "FAILED_ZIP_EXTRACT"`: PHP auto-extract bridge failed. Suggest checking `app_url` or falling back to standard sync by disabling `zip_deploy`.

3. Post-Deployment Verification (Optional/Troubleshooting):
   - To check server error logs if needed:
     ```json
     {
       "project": "my-webapp",
       "environment": "production",
       "remote_path": "storage/logs/laravel.log",
       "tail_lines": 50
     }
     ```
   - To inspect remote directories:
     ```json
     {
       "project": "my-webapp",
       "environment": "production",
       "path": "/public_html"
     }
     ```

---

## 💻 Fallback CLI Commands (When MCP Is Not Connected)

If the MCP server is not active in the current session, instruct the user with standard terminal commands:

| Action | CLI Command |
|---|---|
| Initialize config | `deployctl init` |
| Add credentials | `deployctl credential add <name> --host <host> --username <user> --protocol ftp` |
| Link target | `deployctl project set <project> <env> --credential <name> --remote-path <path>` |
| Test connection | `deployctl test <project> [env]` |
| Preview diff | `deployctl deploy <project> [env] --dry-run` |
| Deploy | `deployctl deploy <project> [env] --yes` |
| Remote explore | `deployctl remote ls <project> [path]` |
| Audit history | `deployctl history [project]` |
