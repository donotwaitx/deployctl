"""MCP (Model Context Protocol) Server for deployctl.

Enables Antigravity and Claude Code to invoke deployment actions
as structured tool calls via stdio JSON-RPC 2.0.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from deployctl.config import load_projects, save_projects
from deployctl.credentials import get_credential
from deployctl.deployer import (
    browse_target_directories,
    download_target_file,
    get_sanitized_config,
    run_deployment,
    test_target_connection,
)

MCP_TOOLS = [
    {
        "name": "deploy_project",
        "description": "Deploy project files to a remote server using credentials securely loaded from macOS Keychain / Linux SecretService / Vault. Never requires or exposes passwords.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "The name of the project to deploy (e.g. my-webapp)",
                },
                "environment": {
                    "type": "string",
                    "description": "Target environment (e.g. production, staging)",
                    "default": "production",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Simulate deployment and calculate diff without uploading any files",
                    "default": False,
                },
                "yes": {
                    "type": "boolean",
                    "description": "Confirm production deployment automatically",
                    "default": True,
                },
                "zip_deploy": {
                    "type": "boolean",
                    "description": "Use fast Zip packaging and remote PHP auto-extraction bridge (ideal for large codebases and vendor folders)",
                    "default": False,
                },
                "app_url": {
                    "type": "string",
                    "description": "Public web URL of the target app for triggering remote PHP extraction bridge",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "list_projects",
        "description": "List all configured projects, target environments, protocols, and credential status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "test_connection",
        "description": "Test connectivity to a remote server target using stored credentials without deploying.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "The name of the project to test",
                },
                "environment": {
                    "type": "string",
                    "description": "Target environment (default: production)",
                    "default": "production",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "show_project",
        "description": "Inspect project deployment configuration. All passwords remain strictly protected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name",
                },
                "environment": {
                    "type": "string",
                    "description": "Environment name (default: production)",
                    "default": "production",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "browse_remote_directories",
        "description": "Inspect and browse actual remote directories and files on the hosting server. Use this to explore what folders exist on the server before picking a remote deployment destination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. my-webapp)",
                },
                "environment": {
                    "type": "string",
                    "description": "Environment name (default: production)",
                    "default": "production",
                },
                "path": {
                    "type": "string",
                    "description": "Remote path to browse (default: /)",
                    "default": "/",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "download_remote_file",
        "description": "Download one file (e.g. storage/logs/laravel.log) from the remote server using Keychain credentials. The file is saved under ~/.deployctl/downloads/<project>/<environment>/ (the response gives local_path, readable with a file tool) and the last lines are returned inline with secrets redacted. Refuses directories and files over max_bytes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. my-webapp)",
                },
                "environment": {
                    "type": "string",
                    "description": "Environment name (default: production)",
                    "default": "production",
                },
                "remote_path": {
                    "type": "string",
                    "description": "Absolute remote file path, as shown by browse_remote_directories",
                },
                "tail_lines": {
                    "type": "integer",
                    "description": "Return this many trailing lines inline (0 = save only, no preview)",
                    "default": 200,
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Refuse files larger than this many bytes (default 52428800 = 50 MB)",
                    "default": 52428800,
                },
            },
            "required": ["project", "remote_path"],
        },
    },
    {
        "name": "set_remote_path",
        "description": "Configure the remote deployment destination directory (remote_path) for a project and environment in projects.yaml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. my-webapp)",
                },
                "environment": {
                    "type": "string",
                    "description": "Environment name (default: production)",
                    "default": "production",
                },
                "remote_path": {
                    "type": "string",
                    "description": "Remote directory path on hosting (e.g. /public_html or /public_html/demo)",
                },
            },
            "required": ["project", "remote_path"],
        },
    },
]


def handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute tool and return MCP formatted content response."""
    try:
        if tool_name == "list_projects":
            data = load_projects()
            projects = data.get("projects", {})
            summary = []
            for p_name, envs in projects.items():
                if isinstance(envs, dict):
                    for e_name, e_cfg in envs.items():
                        cred = e_cfg.get("credential")
                        has_cred = bool(get_credential(cred)) if cred else False
                        summary.append({
                            "project": p_name,
                            "environment": e_name,
                            "protocol": e_cfg.get("protocol", "ftp"),
                            "credential_id": cred,
                            "keychain_status": "STORED" if has_cred else "MISSING",
                        })
            return {"content": [{"type": "text", "text": json.dumps(summary, indent=2)}]}

        elif tool_name == "test_connection":
            project = arguments.get("project", "")
            environment = arguments.get("environment", "production")
            ok, msg = test_target_connection(project, environment)
            return {
                "content": [{"type": "text", "text": f"{'✔ SUCCESS' if ok else '✖ FAILED'}: {msg}"}],
                "isError": not ok,
            }

        elif tool_name == "show_project":
            project = arguments.get("project", "")
            environment = arguments.get("environment", "production")
            cfg = get_sanitized_config(project, environment)
            return {"content": [{"type": "text", "text": json.dumps(cfg, indent=2)}]}

        elif tool_name == "deploy_project":
            project = arguments.get("project", "")
            environment = arguments.get("environment", "production")
            dry_run = arguments.get("dry_run", False)
            yes = arguments.get("yes", True)
            zip_deploy = arguments.get("zip_deploy")
            app_url = arguments.get("app_url")

            ok = run_deployment(
                project=project,
                environment=environment,
                dry_run=dry_run,
                skip_confirm=yes,
                zip_deploy=zip_deploy,
                app_url=app_url,
            )
            status_text = "DRY RUN COMPLETED" if dry_run else ("DEPLOYMENT SUCCESSFUL" if ok else "DEPLOYMENT FAILED")
            return {
                "content": [{"type": "text", "text": status_text}],
                "isError": not ok,
            }

        elif tool_name == "browse_remote_directories":
            project = arguments.get("project", "")
            environment = arguments.get("environment", "production")
            path = arguments.get("path", "/")
            res = browse_target_directories(project, environment, path)
            return {
                "content": [{"type": "text", "text": json.dumps(res, indent=2)}],
                "isError": not res.get("ok", True),
            }

        elif tool_name == "download_remote_file":
            res = download_target_file(
                arguments.get("project", ""),
                arguments.get("environment", "production"),
                arguments.get("remote_path", ""),
                tail_lines=int(arguments.get("tail_lines", 200)),
                max_bytes=int(arguments.get("max_bytes", 52428800)),
            )
            return {
                "content": [{"type": "text", "text": json.dumps(res, indent=2)}],
                "isError": not res.get("ok", True),
            }

        elif tool_name == "set_remote_path":
            project = arguments.get("project", "")
            environment = arguments.get("environment", "production")
            remote_path = arguments.get("remote_path", "").strip()
            if not remote_path:
                return {"content": [{"type": "text", "text": "Error: remote_path cannot be empty"}], "isError": True}

            data = load_projects()
            if "projects" not in data:
                data["projects"] = {}
            if project not in data["projects"]:
                data["projects"][project] = {}
            target_env = None
            for e_name in data["projects"][project]:
                if e_name.lower() == environment.lower():
                    target_env = e_name
                    break
            if not target_env:
                target_env = environment
                data["projects"][project][target_env] = {
                    "protocol": "ftp",
                    "credential": f"{project}-{environment}",
                    "remote_path": remote_path,
                    "local_path": ".",
                }
            else:
                data["projects"][project][target_env]["remote_path"] = remote_path
            save_projects(data)
            return {
                "content": [{"type": "text", "text": f"Successfully updated remote_path to '{remote_path}' for {project}:{target_env}"}],
                "isError": False,
            }

        else:
            return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error executing {tool_name}: {str(e)}"}], "isError": True}


def run_mcp_server() -> None:
    """Run standard stdio MCP JSON-RPC loop."""
    sys.stderr.write("Starting deployctl MCP Server (stdio)...\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "deployctl", "version": "0.1.0"},
                },
            }
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": MCP_TOOLS},
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            call_result = handle_tool_call(tool_name, tool_args)
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_result,
            }
        elif method == "notifications/initialized":
            continue
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
