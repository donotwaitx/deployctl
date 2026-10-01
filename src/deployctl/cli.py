"""Command line interface for deployctl.

Built with Typer and Rich for Claude Code and Antigravity.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from deployctl.config import (
    DELETE_MODES,
    GLOBAL_CONFIG_FILE,
    PROJECTS_FILE,
    init_sample_config,
    load_global_config,
    load_projects,
    save_projects,
)
from deployctl.credentials import (
    delete_credential,
    get_credential,
    list_credentials,
    save_credential,
)
from deployctl.deployer import (
    browse_credential_directories,
    browse_target_directories,
    create_credential_directory,
    create_target_directory,
    get_sanitized_config,
    run_deployment,
    test_target_connection,
)
from deployctl.logger import get_deployment_history
from deployctl.mcp import run_mcp_server

app = typer.Typer(
    name="deployctl",
    help="Secure unified deployment CLI for AI Agents (Antigravity & Claude Code) and developers.",
    no_args_is_help=True,
    add_completion=False,
)

credential_app = typer.Typer(
    name="credential",
    help="Manage server credentials stored securely in macOS Keychain.",
    no_args_is_help=True,
)
app.add_typer(credential_app, name="credential")

project_app = typer.Typer(
    name="project",
    help="Manage project targets and settings (remote_path, credential, protocol).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")

remote_app = typer.Typer(
    name="remote",
    help="Inspect, explore, and browse directories on the remote hosting server.",
    no_args_is_help=True,
)
app.add_typer(remote_app, name="remote")

console = Console()
err_console = Console(stderr=True)


@app.command("list")
def list_projects_cmd():
    """List all configured projects, environments, protocols, and keychain status."""
    data = load_projects()
    projects = data.get("projects", {})

    if not projects:
        console.print(f"[yellow]No projects found in {PROJECTS_FILE}[/yellow]")
        console.print("[dim]Run 'deployctl init' to create a starter configuration.[/dim]")
        return

    table = Table(
        title="Configured Projects & Deployment Targets",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("PROJECT", style="bold white")
    table.add_column("ENV", style="magenta")
    table.add_column("PROTOCOL", style="blue")
    table.add_column("REMOTE PATH", style="white")
    table.add_column("KEYCHAIN", style="green")

    for p_name, envs in projects.items():
        if isinstance(envs, dict) and envs:
            first = True
            env_items = list(envs.items())
            for idx, (e_name, e_cfg) in enumerate(env_items):
                if isinstance(e_cfg, dict):
                    cred_name = e_cfg.get("credential", "-")
                    has_cred = bool(get_credential(cred_name)) if cred_name != "-" else False
                    status_text = "[green]STORED[/green]" if has_cred else "[red]MISSING[/red]"
                    remote_path = e_cfg.get("remote_path", "/")
                    proto = e_cfg.get("protocol", "ftp").upper()
                    is_last = (idx == len(env_items) - 1)

                    table.add_row(
                        p_name if first else "",
                        e_name,
                        proto,
                        remote_path,
                        status_text,
                        end_section=is_last,
                    )
                    first = False

    console.print(table)


@app.command("deploy")
def deploy_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    environment: str = typer.Argument("production", help="Deployment environment (default: production)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate changes without uploading files"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Automatic yes to production confirmation prompt"),
    force: bool = typer.Option(False, "--force", "-f", help="Bypass project isolation and the target's allowed_branches / require_clean policy"),
    local_path: Optional[Path] = typer.Option(None, "--local-path", help="Override local directory source"),
    remote_path: Optional[str] = typer.Option(None, "--remote-path", "-r", help="Override destination remote directory on hosting"),
    zip_deploy: Optional[bool] = typer.Option(None, "--zip/--no-zip", "-z", help="Use Zip archive & Remote PHP Auto-Extract Bridge"),
    app_url: Optional[str] = typer.Option(None, "--app-url", "--url", help="Public web URL of the target app for PHP extraction trigger"),
    remote_scan: bool = typer.Option(False, "--remote-scan", "--fresh", help="Force direct remote network scan instead of local state cache"),
):
    """Deploy project files to the target server."""
    try:
        success = run_deployment(
            project=project,
            environment=environment,
            dry_run=dry_run,
            skip_confirm=yes,
            force_project=force,
            force_branch=force,
            local_path_override=local_path,
            remote_path_override=remote_path,
            zip_deploy=zip_deploy,
            app_url=app_url,
            remote_scan=remote_scan,
        )
        if not success:
            raise typer.Exit(code=1)
    except Exception as e:
        err_console.print(f"[bold red]Deployment failed:[/bold red] {str(e)}")
        raise typer.Exit(code=1)


@app.command("test")
def test_cmd(
    project: str = typer.Argument(..., help="Project name to test"),
    environment: str = typer.Argument("production", help="Target environment"),
):
    """Test connection to remote server using macOS Keychain credentials."""
    console.print(f"[dim]Testing connection to {project}:{environment}...[/dim]")
    try:
        ok, msg = test_target_connection(project, environment)
        if ok:
            console.print(f"[bold green]✔ OK:[/bold green] {msg}")
        else:
            err_console.print(f"[bold red]✖ FAILED:[/bold red] {msg}")
            raise typer.Exit(code=1)
    except Exception as e:
        err_console.print(f"[bold red]✖ Error:[/bold red] {str(e)}")
        raise typer.Exit(code=1)


@app.command("show")
def show_cmd(
    project: str = typer.Argument(..., help="Project name"),
    environment: str = typer.Argument("production", help="Environment name"),
):
    """Display project configuration. NEVER prints secrets."""
    try:
        cfg = get_sanitized_config(project, environment)
        table = Table(
            title=f"Configuration: {project} ({environment})",
            box=box.SIMPLE,
            show_header=False,
        )
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")

        for k, v in cfg.items():
            table.add_row(k.replace("_", " ").title(), str(v))

        console.print(table)
    except Exception as e:
        err_console.print(f"[bold red]Error:[/bold red] {str(e)}")
        raise typer.Exit(code=1)


@app.command("history")
def history_cmd(
    project: Optional[str] = typer.Argument(None, help="Filter by project name"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of records to display"),
):
    """Show past deployment logs and audit history."""
    records = get_deployment_history(project=project, limit=limit)
    if not records:
        console.print("[dim]No deployment history records found.[/dim]")
        return

    table = Table(title="Deployment History", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("TIMESTAMP", style="dim")
    table.add_column("PROJECT", style="bold white")
    table.add_column("ENV", style="magenta")
    table.add_column("STATUS", style="green")
    table.add_column("FILES", justify="right")
    table.add_column("DURATION", justify="right")

    for r in records:
        status_raw = r.get("status", "UNKNOWN")
        status_color = "green" if "SUCCESS" in status_raw else "red"
        table.add_row(
            r.get("timestamp", "")[:19].replace("T", " "),
            r.get("project", ""),
            r.get("environment", ""),
            f"[{status_color}]{status_raw}[/{status_color}]",
            str(r.get("files_count", 0)),
            f"{r.get('duration_seconds', 0)}s",
        )

    console.print(table)


@app.command("init")
def init_cmd():
    """Initialize ~/.deployctl/ configuration and sample projects registry."""
    cfg_file, proj_file = init_sample_config()
    console.print("[bold green]✔ Initialized deployctl directory:[/bold green]")
    console.print(f"  Config   : [dim]{cfg_file}[/dim]")
    console.print(f"  Projects : [dim]{proj_file}[/dim]")


@app.command("mcp")
def mcp_cmd():
    """Start MCP (Model Context Protocol) Server on stdio for Antigravity & Claude Code."""
    run_mcp_server()


# --- Credential subcommands ---


def _run_interactive_browser_for_cred(credential_name: str, protocol: str | None = None, start_path: str = "/") -> str | None:
    current_path = start_path or "/"
    console.print(f"[bold cyan]Connecting to server using credential '{credential_name}'...[/bold cyan]")
    while True:
        with console.status(f"[cyan]Scanning remote path: {current_path}..."):
            res = browse_credential_directories(credential_name, protocol=protocol, path=current_path)

        if not res.get("ok"):
            err_console.print(f"[bold red]✖ {res.get('message')}[/bold red]")
            return None

        current_path = res["current_path"]
        dirs = res.get("directories", [])
        files = res.get("files", [])
        proto = res.get("protocol", "FTP").upper()
        host = res.get("host", "")

        console.print(f"\n[bold green]Connected: {host} ({proto})[/bold green]")
        console.print(f"[bold yellow]📂 Remote Directory:[/bold yellow] [bold cyan]{current_path}[/bold cyan]")

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("TYPE", width=6)
        table.add_column("NAME", style="bold white")
        table.add_column("PATH", style="dim")

        if current_path != "/":
            table.add_row("..", "DIR", "[yellow].. (Go to parent)[/yellow]", res.get("parent_path", "/"))

        for idx, d in enumerate(dirs, 1):
            table.add_row(str(idx), "[bold blue]DIR[/bold blue]", f"📁 {d['name']}", d['path'])

        for f in files[:8]:
            size_kb = round(f.get("size", 0) / 1024, 1)
            table.add_row("-", "[dim]FILE[/dim]", f"📄 {f['name']}", f"{size_kb} KB")
        if len(files) > 8:
            table.add_row("-", "[dim]...[/dim]", f"[dim]+{len(files) - 8} more files[/dim]", "")

        console.print(table)
        console.print(f"[dim]Actions: [bold][1-{len(dirs)}][/bold] open folder | [bold]..[/bold] up | [bold]s[/bold] SELECT this folder | [bold]m[/bold] new folder | [bold]q[/bold] cancel[/dim]")

        choice = Prompt.ask("[bold cyan]Enter choice[/bold cyan]", default="s").strip()
        if choice.lower() == "q":
            console.print("[yellow]Cancelled directory selection.[/yellow]")
            return None
        elif choice.lower() == "s":
            return current_path
        elif choice == "..":
            current_path = res.get("parent_path", "/")
        elif choice.lower() == "m":
            folder_name = Prompt.ask("[cyan]Enter new folder name to create[/cyan]").strip()
            if folder_name:
                import posixpath
                new_full = posixpath.normpath(posixpath.join(current_path, folder_name))
                console.print(f"Creating directory [bold]{new_full}[/bold]...")
                mkdir_res = create_credential_directory(credential_name, protocol=protocol, new_dir=new_full)
                if mkdir_res.get("ok"):
                    console.print(f"[green]✔ Directory created: {new_full}[/green]")
                    current_path = new_full
                else:
                    console.print(f"[red]✖ Failed to create directory: {mkdir_res.get('message')}[/red]")
        elif choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(dirs):
                current_path = dirs[idx - 1]["path"]
            else:
                console.print(f"[red]Invalid folder number: {choice}[/red]")
        else:
            console.print("[yellow]Invalid option. Use number, '..', 's', 'm', or 'q'.[/yellow]")


@credential_app.command("add")
def cred_add(
    name: str = typer.Argument(..., help="Credential identifier (e.g. prod-server)"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Server hostname or IP"),
    username: Optional[str] = typer.Option(None, "--username", "-u", help="Username"),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Password (prompted securely if omitted)"),
    port: Optional[int] = typer.Option(None, "--port", help="Port number"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="Protocol (ftp/ftps/sftp)"),
    key_path: Optional[str] = typer.Option(None, "--key-path", help="Path to SSH private key (for SFTP)"),
    project: Optional[str] = typer.Option(None, "--project", help="Link/create a project target name immediately"),
    environment: str = typer.Option("production", "--env", "-e", help="Environment name for linked project"),
    remote_path: Optional[str] = typer.Option(None, "--remote-path", "-r", help="Set remote destination directory"),
    browse: Optional[bool] = typer.Option(None, "--browse/--no-browse", "-b", help="Interactively browse remote server to pick remote_path"),
):
    """Store server credentials securely into macOS Keychain and optionally configure remote_path / project."""
    h = host or Prompt.ask("Host")
    u = username or Prompt.ask("Username")
    p = password or Prompt.ask("Password", password=True)
    proto = protocol or Prompt.ask("Protocol", choices=["ftp", "ftps", "sftp", "local"], default="ftp")

    port_num = port
    if not port_num:
        default_port = "22" if proto == "sftp" else ("990" if proto == "ftps" else "21")
        port_in = Prompt.ask("Port", default=default_port)
        port_num = int(port_in) if port_in.isdigit() else None

    kp = key_path
    if proto == "sftp" and not kp and not p:
        kp = Prompt.ask("SSH Key Path", default="")

    ok = save_credential(
        name=name,
        host=h,
        username=u,
        password=p,
        port=port_num,
        protocol=proto,
        key_path=kp,
    )

    if not ok:
        err_console.print(f"[bold red]✖ Failed to save credential '{name}' to macOS Keychain.[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✔ Saved credential '{name}' into macOS Keychain.[/bold green]")
    console.print(f"[dim]Identity: {u}@{h}:{port_num} ({proto.upper()})[/dim]")

    # Check if user wants to link a project / select remote path
    suggested_project_name = name
    for suffix in ["-prod", "-production", "-stage", "-staging", "-demo", "-dev"]:
        if suggested_project_name.lower().endswith(suffix):
            suggested_project_name = suggested_project_name[:-len(suffix)]
            break

    should_link = False
    if project is not None or remote_path is not None or browse is not None:
        should_link = True
    elif sys.stdin.isatty():
        should_link = Confirm.ask(
            f"\nDo you want to configure remote path and link a project target for '[bold cyan]{name}[/bold cyan]' now?",
            default=True,
        )

    if should_link:
        proj_name = project or Prompt.ask("Project name", default=suggested_project_name)
        env_name = environment if project else Prompt.ask("Environment", default="production")

        selected_remote = remote_path
        if selected_remote is None:
            do_browse = browse
            if do_browse is None and sys.stdin.isatty():
                do_browse = Confirm.ask("Browse remote server directories now to pick destination path?", default=True)

            if do_browse:
                selected_remote = _run_interactive_browser_for_cred(name, protocol=proto, start_path="/")

            if not selected_remote:
                selected_remote = Prompt.ask("Remote path", default="/public_html")

        # Save to projects.yaml
        data = load_projects()
        if "projects" not in data:
            data["projects"] = {}
        if proj_name not in data["projects"]:
            data["projects"][proj_name] = {}

        data["projects"][proj_name][env_name] = {
            "protocol": proto,
            "credential": name,
            "remote_path": selected_remote,
            "local_path": ".",
        }
        save_projects(data)
        console.print(f"\n[bold green]✔ Linked Project Target:[/bold green]")
        console.print(f"  Project     : [bold cyan]{proj_name}[/bold cyan]")
        console.print(f"  Environment : [magenta]{env_name}[/magenta]")
        console.print(f"  Remote Path : [bold green]{selected_remote}[/bold green]")
        console.print(f"  Credential  : [dim]{name}[/dim]")


@credential_app.command("list")
def cred_list():
    """List all credential identifiers stored in macOS Keychain."""
    names = list_credentials()
    if not names:
        console.print("[dim]No credentials stored yet. Run 'deployctl credential add <name>' to create one.[/dim]")
        return

    table = Table(title="macOS Keychain Credentials", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("CREDENTIAL NAME", style="bold white")
    table.add_column("HOST", style="cyan")
    table.add_column("USER", style="magenta")
    table.add_column("PROTOCOL", style="blue")
    table.add_column("PASSWORD", style="dim")

    for name in names:
        cred = get_credential(name)
        if cred:
            table.add_row(
                name,
                cred.get("host") or "-",
                cred.get("username") or "-",
                (cred.get("protocol") or "ftp").upper(),
                "●●●●●●●● (Keychain)",
            )
        else:
            table.add_row(name, "-", "-", "-", "[red]Not found[/red]")

    console.print(table)


@credential_app.command("delete")
def cred_delete(
    name: str = typer.Argument(..., help="Credential identifier to remove"),
):
    """Delete a credential from macOS Keychain."""
    ok = delete_credential(name)
    if ok:
        console.print(f"[bold green]✔ Credential '{name}' deleted from macOS Keychain.[/bold green]")
    else:
        err_console.print(f"[bold red]✖ Credential '{name}' not found or could not be removed.[/bold red]")
        raise typer.Exit(code=1)


# --- Project subcommands ---


@project_app.command("set")
@app.command("set")
def project_set_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    environment: str = typer.Argument("production", help="Target environment (default: production)"),
    remote_path: Optional[str] = typer.Option(None, "--remote-path", "-r", help="Set remote destination directory on hosting"),
    credential: Optional[str] = typer.Option(None, "--credential", "-c", help="Set credential identifier from Keychain"),
    protocol: Optional[str] = typer.Option(None, "--protocol", help="Set protocol (ftp/ftps/sftp/local)"),
    local_path: Optional[str] = typer.Option(None, "--local-path", "-l", help="Set local directory path"),
    zip_deploy: Optional[bool] = typer.Option(None, "--zip/--no-zip", "-z", help="Enable/disable Zip & PHP extraction bridge strategy"),
    app_url: Optional[str] = typer.Option(None, "--app-url", "--url", help="Public web URL of the target app for PHP extraction trigger"),
    delete_missing: Optional[str] = typer.Option(None, "--delete-missing", help="Which server files a deploy may delete: owned (default, only files deployctl deployed), all, or none"),
):
    """Set or update project settings (remote_path, credential, protocol, zip_deploy, app_url, delete_missing) directly via CLI."""
    if delete_missing is not None and delete_missing.lower() not in DELETE_MODES:
        err_console.print(f"[bold red]--delete-missing must be one of: {', '.join(DELETE_MODES)}[/bold red]")
        raise typer.Exit(code=1)
    data = load_projects()
    if "projects" not in data:
        data["projects"] = {}

    if project not in data["projects"]:
        data["projects"][project] = {}

    # Find environment (case-insensitive or alias)
    target_env = None
    for e_name in data["projects"][project]:
        if e_name.lower() == environment.lower():
            target_env = e_name
            break

    if not target_env:
        alias_map = {"prod": "production", "production": "prod", "stage": "staging", "staging": "stage"}
        alt = alias_map.get(environment.lower())
        if alt:
            for e_name in data["projects"][project]:
                if e_name.lower() == alt:
                    target_env = e_name
                    break

    if not target_env:
        target_env = environment
        data["projects"][project][target_env] = {
            "protocol": protocol or "ftp",
            "credential": credential or f"{project}-{environment}",
            "remote_path": remote_path or "/public_html",
            "local_path": local_path or ".",
        }
        if zip_deploy is not None:
            data["projects"][project][target_env]["zip_deploy"] = zip_deploy
        if app_url is not None:
            data["projects"][project][target_env]["app_url"] = app_url
        if delete_missing is not None:
            data["projects"][project][target_env]["delete_missing"] = delete_missing.lower()
    else:
        env_dict = data["projects"][project][target_env]
        if remote_path is not None:
            env_dict["remote_path"] = remote_path
        if credential is not None:
            env_dict["credential"] = credential
        if protocol is not None:
            env_dict["protocol"] = protocol
        if local_path is not None:
            env_dict["local_path"] = local_path
        if zip_deploy is not None:
            env_dict["zip_deploy"] = zip_deploy
        if app_url is not None:
            env_dict["app_url"] = app_url
        if delete_missing is not None:
            env_dict["delete_missing"] = delete_missing.lower()

    save_projects(data)
    curr = data["projects"][project][target_env]
    console.print(f"[bold green]✔ Configured {project}:{target_env}:[/bold green]")
    console.print(f"  Remote Path : [bold cyan]{curr.get('remote_path')}[/bold cyan]")
    console.print(f"  Credential  : [dim]{curr.get('credential')}[/dim]")
    console.print(f"  Protocol    : [blue]{curr.get('protocol', 'ftp').upper()}[/blue]")
    console.print(f"  Local Path  : [dim]{curr.get('local_path', '.')}[/dim]")
    if curr.get("zip_deploy"):
        console.print(f"  Zip Deploy  : [magenta]ENABLED (PHP Auto-Extract)[/magenta]")
    if curr.get("app_url"):
        console.print(f"  App Web URL : [green]{curr.get('app_url')}[/green]")


@project_app.command("add")
def project_add_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    environment: str = typer.Argument("production", help="Environment (default: production)"),
    remote_path: str = typer.Option("/public_html", "--remote-path", "-r", help="Remote directory path on hosting"),
    credential: Optional[str] = typer.Option(None, "--credential", "-c", help="Credential identifier from Keychain"),
    protocol: str = typer.Option("ftp", "--protocol", help="Protocol (ftp/ftps/sftp/local)"),
    local_path: str = typer.Option(".", "--local-path", "-l", help="Local directory path"),
    zip_deploy: bool = typer.Option(False, "--zip/--no-zip", "-z", help="Enable Zip & PHP extraction bridge strategy"),
    app_url: Optional[str] = typer.Option(None, "--app-url", "--url", help="Public web URL of the target app for PHP extraction trigger"),
):
    """Add a new project or environment target to ~/.deployctl/projects.yaml."""
    data = load_projects()
    if "projects" not in data:
        data["projects"] = {}
    if project not in data["projects"]:
        data["projects"][project] = {}

    cred_name = credential or f"{project}-{environment}"
    env_data: dict[str, Any] = {
        "protocol": protocol,
        "credential": cred_name,
        "remote_path": remote_path,
        "local_path": local_path,
    }
    if zip_deploy:
        env_data["zip_deploy"] = True
    if app_url:
        env_data["app_url"] = app_url

    data["projects"][project][environment] = env_data
    save_projects(data)
    console.print(f"[bold green]✔ Added {project}:{environment} -> {remote_path} (Credential: {cred_name})[/bold green]")
    if zip_deploy:
        console.print(f"  [dim]Strategy: Zip & Remote PHP Auto-Extract[/dim]")


@project_app.command("delete")
def project_delete_cmd(
    project: str = typer.Argument(..., help="Project name to delete"),
    environment: Optional[str] = typer.Argument(None, help="Specific environment to delete (omits entire project if omitted)"),
):
    """Delete a project or specific environment from ~/.deployctl/projects.yaml."""
    data = load_projects()
    projects = data.get("projects", {})
    if project not in projects:
        err_console.print(f"[bold red]Project '{project}' not found.[/bold red]")
        raise typer.Exit(code=1)

    if environment:
        if environment in projects[project]:
            del projects[project][environment]
            if not projects[project]:
                del projects[project]
            save_projects(data)
            console.print(f"[bold green]✔ Deleted environment '{environment}' from project '{project}'.[/bold green]")
        else:
            err_console.print(f"[bold red]Environment '{environment}' not found in project '{project}'.[/bold red]")
            raise typer.Exit(code=1)
    else:
        del projects[project]
        save_projects(data)
        console.print(f"[bold green]✔ Deleted project '{project}' from projects.yaml.[/bold green]")


# --- Remote exploration commands ---


def _run_interactive_browser(project: str, environment: str = "production", start_path: str = "/") -> str | None:
    current_path = start_path or "/"
    console.print(f"[bold cyan]Connecting to {project}:{environment} using Keychain credentials...[/bold cyan]")
    while True:
        with console.status(f"[cyan]Scanning remote path: {current_path}..."):
            res = browse_target_directories(project, environment, current_path)

        if not res.get("ok"):
            err_console.print(f"[bold red]✖ {res.get('message')}[/bold red]")
            return None

        current_path = res["current_path"]
        dirs = res.get("directories", [])
        files = res.get("files", [])
        proto = res.get("protocol", "FTP").upper()
        host = res.get("host", "")

        console.print(f"\n[bold green]Connected: {host} ({proto})[/bold green]")
        console.print(f"[bold yellow]📂 Remote Directory:[/bold yellow] [bold cyan]{current_path}[/bold cyan]")

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("TYPE", width=6)
        table.add_column("NAME", style="bold white")
        table.add_column("PATH", style="dim")

        if current_path != "/":
            table.add_row("..", "DIR", "[yellow].. (Go to parent)[/yellow]", res.get("parent_path", "/"))

        for idx, d in enumerate(dirs, 1):
            table.add_row(str(idx), "[bold blue]DIR[/bold blue]", f"📁 {d['name']}", d['path'])

        for f in files[:8]:
            size_kb = round(f.get("size", 0) / 1024, 1)
            table.add_row("-", "[dim]FILE[/dim]", f"📄 {f['name']}", f"{size_kb} KB")
        if len(files) > 8:
            table.add_row("-", "[dim]...[/dim]", f"[dim]+{len(files) - 8} more files[/dim]", "")

        console.print(table)
        console.print(f"[dim]Actions: [bold][1-{len(dirs)}][/bold] open folder | [bold]..[/bold] up | [bold]s[/bold] SELECT this folder | [bold]m[/bold] new folder | [bold]q[/bold] cancel[/dim]")

        choice = Prompt.ask("[bold cyan]Enter choice[/bold cyan]", default="s").strip()
        if choice.lower() == "q":
            console.print("[yellow]Cancelled directory selection.[/yellow]")
            return None
        elif choice.lower() == "s":
            return current_path
        elif choice == "..":
            current_path = res.get("parent_path", "/")
        elif choice.lower() == "m":
            folder_name = Prompt.ask("[cyan]Enter new folder name to create[/cyan]").strip()
            if folder_name:
                import posixpath
                new_full = posixpath.normpath(posixpath.join(current_path, folder_name))
                console.print(f"Creating directory [bold]{new_full}[/bold]...")
                mkdir_res = create_target_directory(project, environment, new_full)
                if mkdir_res.get("ok"):
                    console.print(f"[green]✔ Directory created: {new_full}[/green]")
                    current_path = new_full
                else:
                    console.print(f"[red]✖ Failed to create directory: {mkdir_res.get('message')}[/red]")
        elif choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(dirs):
                current_path = dirs[idx - 1]["path"]
            else:
                console.print(f"[red]Invalid folder number: {choice}[/red]")
        else:
            console.print("[yellow]Invalid option. Use number, '..', 's', 'm', or 'q'.[/yellow]")


@remote_app.command("ls")
def remote_ls_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    environment: str = typer.Argument("production", help="Environment (default: production)"),
    path: str = typer.Argument("/", help="Remote path to list (default: /)"),
):
    """List files and subdirectories on the remote hosting server."""
    with console.status(f"[cyan]Connecting and scanning {project}:{environment} at '{path}'..."):
        res = browse_target_directories(project, environment, path)

    if not res.get("ok"):
        err_console.print(f"[bold red]✖ {res.get('message')}[/bold red]")
        raise typer.Exit(code=1)

    table = Table(
        title=f"Remote Server Contents: {project}:{environment} ({res.get('current_path')})",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("TYPE", width=6)
    table.add_column("NAME", style="bold white")
    table.add_column("REMOTE PATH", style="cyan")
    table.add_column("SIZE", style="dim")

    for d in res.get("directories", []):
        table.add_row("[bold blue]DIR[/bold blue]", f"📁 {d['name']}", d['path'], "-")

    for f in res.get("files", []):
        size_kb = round(f.get("size", 0) / 1024, 1)
        table.add_row("[dim]FILE[/dim]", f"📄 {f['name']}", f['path'], f"{size_kb} KB")

    console.print(table)


@remote_app.command("select")
@app.command("select-path")
def remote_select_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    environment: str = typer.Argument("production", help="Environment (default: production)"),
    path: str = typer.Option("/", "--path", "-p", help="Starting remote path to browse"),
):
    """Interactively browse remote server directories and set remote_path for the project."""
    selected_path = _run_interactive_browser(project, environment, path)
    if not selected_path:
        return

    console.print(f"\n[bold yellow]Selected Remote Path:[/bold yellow] [bold green]{selected_path}[/bold green]")
    save = Confirm.ask(f"Save '{selected_path}' as remote_path for {project}:{environment} in projects.yaml?", default=True)
    if save:
        data = load_projects()
        if "projects" not in data:
            data["projects"] = {}
        if project not in data["projects"]:
            data["projects"][project] = {}

        # Locate environment
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
                "remote_path": selected_path,
                "local_path": ".",
            }
        else:
            data["projects"][project][target_env]["remote_path"] = selected_path

        save_projects(data)
        console.print(f"[bold green]✔ Saved remote_path = '{selected_path}' for {project}:{target_env} in ~/.deployctl/projects.yaml![/bold green]")


@remote_app.command("mkdir")
def remote_mkdir_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. my-app)"),
    path: str = typer.Argument(..., help="Remote directory path to create (e.g. /public_html/demo)"),
    environment: str = typer.Argument("production", help="Environment (default: production)"),
):
    """Create a new directory remotely on the hosting server."""
    with console.status(f"[cyan]Creating remote directory '{path}'..."):
        res = create_target_directory(project, environment, path)

    if res.get("ok"):
        console.print(f"[bold green]✔ Remote directory created:[/bold green] [bold cyan]{path}[/bold cyan]")
    else:
        err_console.print(f"[bold red]✖ Failed to create remote directory: {res.get('message')}[/bold red]")
        raise typer.Exit(code=1)


@remote_app.command("ls-cred")
def remote_ls_cred_cmd(
    credential: str = typer.Argument(..., help="Keychain credential identifier"),
    path: str = typer.Argument("/", help="Remote path to list (default: /)"),
    protocol: str = typer.Option("ftp", "--protocol", help="Protocol (ftp/ftps/sftp/local)"),
):
    """List remote directories using a Keychain credential directly (before configuring project)."""
    with console.status(f"[cyan]Connecting with credential '{credential}' at '{path}'..."):
        res = browse_credential_directories(credential, protocol=protocol, path=path)

    if not res.get("ok"):
        err_console.print(f"[bold red]✖ {res.get('message')}[/bold red]")
        raise typer.Exit(code=1)

    table = Table(
        title=f"Remote Contents ({credential} -> {res.get('current_path')})",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    table.add_column("TYPE", width=6)
    table.add_column("NAME", style="bold white")
    table.add_column("REMOTE PATH", style="cyan")
    table.add_column("SIZE", style="dim")

    for d in res.get("directories", []):
        table.add_row("[bold blue]DIR[/bold blue]", f"📁 {d['name']}", d['path'], "-")

    for f in res.get("files", []):
        size_kb = round(f.get("size", 0) / 1024, 1)
        table.add_row("[dim]FILE[/dim]", f"📄 {f['name']}", f['path'], f"{size_kb} KB")

    console.print(table)


@remote_app.command("scan")
def remote_scan_cmd(
    project: str = typer.Argument(..., help="Project name (e.g. test)"),
    environment: str = typer.Argument("production", help="Environment (default: production)"),
):
    """Fast-sync remote manifest into local state cache using PHP Bridge or Network traversal."""
    try:
        success = run_deployment(
            project=project,
            environment=environment,
            dry_run=True,
            remote_scan=True,
        )
        if not success:
            raise typer.Exit(code=1)
    except Exception as e:
        err_console.print(f"[bold red]Remote scan failed:[/bold red] {str(e)}")
        raise typer.Exit(code=1)

