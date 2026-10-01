"""Deployment orchestration engine for deployctl.

Coordinates credential retrieval from Keychain, diff calculations,
production confirmation safeguards, and provider file synchronization.
"""

from __future__ import annotations

import posixpath
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt

from deployctl.config import (
    DOWNLOADS_DIR,
    get_project_target,
    load_global_config,
    load_projects,
    save_projects,
)
from deployctl.credentials import get_credential
from deployctl.diff import compute_diff, format_diff_text, scan_local_files
from deployctl.logger import DeployLogger
from deployctl.providers import get_provider
from deployctl.security import check_project_isolation, mask_secret, sanitize_text
from deployctl.state import load_deployment_state, save_deployment_state
from deployctl.zip_deploy import (
    create_deployment_zip,
    fetch_remote_manifest,
    generate_php_bridge_script,
    generate_php_scan_script,
    trigger_remote_extraction,
)

console = Console()


def test_target_connection(project: str, environment: str = "production") -> tuple[bool, str]:
    """Test connection to the target server without deploying."""
    env_cfg = get_project_target(project, environment)
    cred_name = env_cfg.get("credential")
    if not cred_name:
        return False, f"No credential configured for {project}:{environment}"

    cred = get_credential(cred_name)
    if not cred:
        return False, f"Credential '{cred_name}' not found in macOS Keychain. Run: deployctl credential add {cred_name}"

    protocol = env_cfg.get("protocol", cred.get("protocol") or "ftp")
    host = cred.get("host") or env_cfg.get("host", "")
    username = cred.get("username") or env_cfg.get("username", "")
    password = cred.get("password")
    port = cred.get("port") or env_cfg.get("port")
    key_path = cred.get("key_path") or env_cfg.get("key_path")
    remote_path = env_cfg.get("remote_path", "/")

    provider = get_provider(
        protocol=protocol,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path=remote_path,
    )
    return provider.test_connection()


def browse_target_directories(
    project: str,
    environment: str = "production",
    path: str = "/",
) -> dict[str, Any]:
    """Browse directories and files on the target server using configured Keychain credentials."""
    try:
        env_cfg = get_project_target(project, environment)
    except Exception as e:
        return {"ok": False, "message": str(e), "current_path": path, "items": [], "directories": [], "files": []}

    cred_name = env_cfg.get("credential")
    if not cred_name:
        return {"ok": False, "message": f"No credential configured for {project}:{environment}", "items": [], "directories": [], "files": []}

    cred = get_credential(cred_name)
    if not cred:
        return {"ok": False, "message": f"Credential '{cred_name}' not found in macOS Keychain", "items": [], "directories": [], "files": []}

    protocol = env_cfg.get("protocol", cred.get("protocol") or "ftp")
    host = cred.get("host") or env_cfg.get("host", "")
    username = cred.get("username") or env_cfg.get("username", "")
    password = cred.get("password")
    port = cred.get("port") or env_cfg.get("port")
    key_path = cred.get("key_path") or env_cfg.get("key_path")

    clean_path = path.strip() if path and path.strip() else "/"
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    clean_path = posixpath.normpath(clean_path)

    provider = get_provider(
        protocol=protocol,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        items = provider.list_dir(clean_path)
        provider.close()

        parent_path = posixpath.dirname(clean_path)
        if not parent_path:
            parent_path = "/"

        return {
            "ok": True,
            "project": project,
            "environment": environment,
            "protocol": protocol,
            "host": host,
            "username": username,
            "current_path": clean_path,
            "parent_path": parent_path,
            "items": items,
            "directories": [i for i in items if i["is_dir"]],
            "files": [i for i in items if not i["is_dir"]],
        }
    except Exception as e:
        provider.close()
        return {
            "ok": False,
            "message": f"Remote browse error ({protocol.upper()}): {str(e)}",
            "current_path": clean_path,
            "items": [],
            "directories": [],
            "files": [],
        }


def browse_credential_directories(
    credential_name: str,
    protocol: str | None = None,
    path: str = "/",
) -> dict[str, Any]:
    """Browse directories directly using a Keychain credential (useful before saving project)."""
    cred = get_credential(credential_name)
    if not cred:
        return {"ok": False, "message": f"Credential '{credential_name}' not found in macOS Keychain", "items": [], "directories": [], "files": []}

    proto = protocol or cred.get("protocol") or "ftp"
    host = cred.get("host", "")
    username = cred.get("username", "")
    password = cred.get("password")
    port = cred.get("port")
    key_path = cred.get("key_path")

    clean_path = path.strip() if path and path.strip() else "/"
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    clean_path = posixpath.normpath(clean_path)

    provider = get_provider(
        protocol=proto,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        items = provider.list_dir(clean_path)
        provider.close()

        parent_path = posixpath.dirname(clean_path)
        if not parent_path:
            parent_path = "/"

        return {
            "ok": True,
            "credential": credential_name,
            "protocol": proto,
            "host": host,
            "username": username,
            "current_path": clean_path,
            "parent_path": parent_path,
            "items": items,
            "directories": [i for i in items if i["is_dir"]],
            "files": [i for i in items if not i["is_dir"]],
        }
    except Exception as e:
        provider.close()
        return {
            "ok": False,
            "message": f"Remote browse error ({proto.upper()}): {str(e)}",
            "current_path": clean_path,
            "items": [],
            "directories": [],
            "files": [],
        }


def browse_connection_directories(
    host: str,
    username: str,
    password: str | None = None,
    protocol: str = "ftp",
    port: int | None = None,
    key_path: str | None = None,
    path: str = "/",
) -> dict[str, Any]:
    """Browse directories directly using provided connection arguments."""
    proto = protocol or "ftp"
    clean_path = path.strip() if path and path.strip() else "/"
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path
    clean_path = posixpath.normpath(clean_path)

    provider = get_provider(
        protocol=proto,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        items = provider.list_dir(clean_path)
        provider.close()

        parent_path = posixpath.dirname(clean_path)
        if not parent_path:
            parent_path = "/"

        return {
            "ok": True,
            "protocol": proto,
            "host": host,
            "username": username,
            "current_path": clean_path,
            "parent_path": parent_path,
            "items": items,
            "directories": [i for i in items if i["is_dir"]],
            "files": [i for i in items if not i["is_dir"]],
        }
    except Exception as e:
        provider.close()
        return {
            "ok": False,
            "message": f"Remote browse error ({proto.upper()}): {str(e)}",
            "current_path": clean_path,
            "items": [],
            "directories": [],
            "files": [],
        }


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_PREVIEW_BYTES = 64 * 1024


def _read_tail_preview(local_file: Path, tail_lines: int) -> str:
    """Return the last `tail_lines` lines of a downloaded file, secrets redacted.

    Only the last MAX_DOWNLOAD_PREVIEW_BYTES bytes are read, so a huge log cannot flood the agent context.
    """
    size = local_file.stat().st_size
    with open(local_file, "rb") as f:
        f.seek(max(0, size - MAX_DOWNLOAD_PREVIEW_BYTES))
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    return sanitize_text("\n".join(text.splitlines()[-tail_lines:]))


def download_connection_file(
    host: str,
    username: str,
    remote_path: str,
    password: str | None = None,
    protocol: str = "ftp",
    port: int | None = None,
    key_path: str | None = None,
    download_dir: Path | None = None,
    tail_lines: int = 200,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """Download one remote file into `download_dir`, mirroring its remote path.

    The local destination is always derived from `remote_path` under `download_dir` (never chosen by the
    caller), so a download cannot overwrite files elsewhere on this machine. Refuses directories and files
    larger than `max_bytes`.
    """
    proto = protocol or "ftp"
    clean = (remote_path or "").strip()
    if not clean or clean.endswith("/"):
        return {"ok": False, "message": "remote_path must point to a file, not a directory"}
    if not clean.startswith("/"):
        clean = "/" + clean
    clean = posixpath.normpath(clean)
    if clean == "/":
        return {"ok": False, "message": "remote_path must point to a file, not a directory"}

    root = (download_dir or DOWNLOADS_DIR).expanduser().resolve()
    local_file = (root / clean.lstrip("/")).resolve()
    if root not in local_file.parents:
        return {"ok": False, "message": f"Refusing to write outside {root}"}

    provider = get_provider(
        protocol=proto,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        name = posixpath.basename(clean)
        entry = next(
            (i for i in provider.list_dir(posixpath.dirname(clean) or "/") if i["name"] == name),
            None,
        )
        if entry is None:
            return {"ok": False, "message": f"Remote file not found: {clean}"}
        if entry["is_dir"]:
            return {"ok": False, "message": f"{clean} is a directory; use browse_remote_directories"}
        if entry["size"] > max_bytes:
            return {
                "ok": False,
                "message": f"{clean} is {entry['size']} bytes, over the {max_bytes} byte limit",
            }

        if not provider.download_file(clean.lstrip("/"), local_file):
            return {"ok": False, "message": f"Download failed: {clean}"}

        size = local_file.stat().st_size
        result: dict[str, Any] = {
            "ok": True,
            "remote_path": clean,
            "local_path": str(local_file),
            "size": size,
        }
        if tail_lines > 0:
            result["tail_lines"] = tail_lines
            result["preview"] = _read_tail_preview(local_file, tail_lines)
        return result
    except Exception as e:
        return {"ok": False, "message": f"Remote download error ({proto.upper()}): {sanitize_text(str(e))}"}
    finally:
        provider.close()


def download_target_file(
    project: str,
    environment: str = "production",
    remote_path: str = "",
    tail_lines: int = 200,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """Download a file from a configured target using its Keychain credentials.

    Files land in ~/.deployctl/downloads/<project>/<environment>/<remote path>.
    """
    try:
        env_cfg = get_project_target(project, environment)
    except Exception as e:
        return {"ok": False, "message": str(e)}

    cred_name = env_cfg.get("credential")
    if not cred_name:
        return {"ok": False, "message": f"No credential configured for {project}:{environment}"}

    cred = get_credential(cred_name)
    if not cred:
        return {"ok": False, "message": f"Credential '{cred_name}' not found in macOS Keychain"}

    return download_connection_file(
        host=cred.get("host") or env_cfg.get("host", ""),
        username=cred.get("username") or env_cfg.get("username", ""),
        password=cred.get("password"),
        protocol=env_cfg.get("protocol", cred.get("protocol") or "ftp"),
        port=cred.get("port") or env_cfg.get("port"),
        key_path=cred.get("key_path") or env_cfg.get("key_path"),
        remote_path=remote_path,
        download_dir=DOWNLOADS_DIR / project / environment,
        tail_lines=tail_lines,
        max_bytes=max_bytes,
    )


def create_target_directory(
    project: str,
    environment: str = "production",
    new_dir: str = "",
) -> dict[str, Any]:
    """Create a new directory remotely on the target server."""
    env_cfg = get_project_target(project, environment)
    cred_name = env_cfg.get("credential")
    if not cred_name:
        return {"ok": False, "message": f"No credential configured for {project}:{environment}"}

    cred = get_credential(cred_name)
    if not cred:
        return {"ok": False, "message": f"Credential '{cred_name}' not found in macOS Keychain"}

    protocol = env_cfg.get("protocol", cred.get("protocol") or "ftp")
    host = cred.get("host") or env_cfg.get("host", "")
    username = cred.get("username") or env_cfg.get("username", "")
    password = cred.get("password")
    port = cred.get("port") or env_cfg.get("port")
    key_path = cred.get("key_path") or env_cfg.get("key_path")

    provider = get_provider(
        protocol=protocol,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        ok = provider.create_dir(new_dir)
        provider.close()
        return {"ok": ok, "created": new_dir}
    except Exception as e:
        provider.close()
        return {"ok": False, "message": str(e)}


def create_credential_directory(
    credential_name: str,
    protocol: str | None = None,
    new_dir: str = "",
) -> dict[str, Any]:
    """Create a new directory remotely using a Keychain credential."""
    cred = get_credential(credential_name)
    if not cred:
        return {"ok": False, "message": f"Credential '{credential_name}' not found in macOS Keychain"}

    proto = protocol or cred.get("protocol") or "ftp"
    host = cred.get("host", "")
    username = cred.get("username", "")
    password = cred.get("password")
    port = cred.get("port")
    key_path = cred.get("key_path")

    provider = get_provider(
        protocol=proto,
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        ok = provider.create_dir(new_dir)
        provider.close()
        return {"ok": ok, "created": new_dir}
    except Exception as e:
        provider.close()
        return {"ok": False, "message": str(e)}


def create_connection_directory(
    host: str,
    username: str,
    password: str | None = None,
    protocol: str = "ftp",
    port: int | None = None,
    key_path: str | None = None,
    new_dir: str = "",
) -> dict[str, Any]:
    """Create a new directory remotely using direct connection credentials."""
    provider = get_provider(
        protocol=protocol or "ftp",
        host=host,
        username=username,
        password=password,
        port=port,
        key_path=key_path,
        remote_path="/",
    )

    try:
        provider.connect()
        ok = provider.create_dir(new_dir)
        provider.close()
        return {"ok": ok, "created": new_dir}
    except Exception as e:
        provider.close()
        return {"ok": False, "message": str(e)}


def get_sanitized_config(project: str, environment: str = "production") -> dict[str, Any]:
    """Retrieve target configuration with all secrets strictly omitted or masked."""
    env_cfg = get_project_target(project, environment)
    cred_name = env_cfg.get("credential")
    cred = get_credential(cred_name) if cred_name else None

    sanitized = {
        "project": env_cfg.get("project", project),
        "environment": env_cfg.get("environment", environment),
        "protocol": env_cfg.get("protocol", "ftp"),
        "credential_name": cred_name,
        "credential_status": "FOUND IN KEYCHAIN" if cred else "MISSING FROM KEYCHAIN",
        "remote_path": env_cfg.get("remote_path", "/"),
        "local_path": env_cfg.get("local_path", "."),
        "host": (cred.get("host") if cred else None) or env_cfg.get("host", "(hidden/in-keychain)"),
        "username": (cred.get("username") if cred else None) or env_cfg.get("username", "(hidden/in-keychain)"),
        "password": "[PROTECTED IN KEYCHAIN - NEVER DISPLAYED]",
        "exclude_patterns_count": len(env_cfg.get("exclude", [])),
    }
    return sanitized


def run_deployment(
    project: str,
    environment: str = "production",
    dry_run: bool = False,
    skip_confirm: bool = False,
    force_project: bool = False,
    local_path_override: Path | None = None,
    remote_path_override: str | None = None,
    zip_deploy: bool | None = None,
    app_url: str | None = None,
    remote_scan: bool = False,
) -> bool:
    """Execute end-to-end deployment workflow."""
    start_time = time.time()

    # 1. Load config
    env_cfg = get_project_target(project, environment)
    global_cfg = load_global_config()

    # Determine zip deploy mode
    is_zip_mode = zip_deploy if zip_deploy is not None else bool(env_cfg.get("zip_deploy", False) or env_cfg.get("strategy") == "zip")
    effective_app_url = app_url or env_cfg.get("app_url")

    # 2. Tier 2: Project Isolation Check
    if global_cfg.get("enforce_project_isolation", False) and not force_project:
        is_isolated, iso_msg = check_project_isolation(project, env_cfg)
        if not is_isolated:
            console.print(f"[bold red]⚠ Project Isolation Alert:[/bold red] {iso_msg}")
            console.print("[dim]Use --force to bypass project isolation if intentional.[/dim]")
            return False

    # 3. Tier 1: Credential Retrieval from Keychain
    cred_name = env_cfg.get("credential")
    if not cred_name:
        console.print(f"[bold red]Error:[/bold red] No credential identifier defined for {project}:{environment}")
        return False

    cred = get_credential(cred_name)
    if not cred:
        console.print(f"[bold red]Error:[/bold red] Credential '{cred_name}' not found in macOS Keychain.")
        console.print(f"[dim]Run: deployctl credential add {cred_name}[/dim]")
        return False

    host = cred.get("host") or env_cfg.get("host", "")
    username = cred.get("username") or env_cfg.get("username", "")
    password = cred.get("password")
    port = cred.get("port") or env_cfg.get("port")
    key_path = cred.get("key_path") or env_cfg.get("key_path")
    protocol = env_cfg.get("protocol", cred.get("protocol") or "ftp")
    remote_path = remote_path_override or env_cfg.get("remote_path", "/")

    # If in Zip deploy mode and app_url is missing, ask or guess
    if is_zip_mode and not effective_app_url:
        guess_url = f"http://{host}"
        if sys.stdin.isatty():
            effective_app_url = Prompt.ask(
                "Enter App Web URL for remote PHP extraction (e.g. https://example.com)",
                default=guess_url,
            )
            # Save app_url to projects.yaml for future runs
            try:
                pdata = load_projects()
                if project in pdata.get("projects", {}) and environment in pdata["projects"][project]:
                    pdata["projects"][project][environment]["app_url"] = effective_app_url
                    save_projects(pdata)
            except Exception:
                pass
        else:
            effective_app_url = guess_url

    # Setup logger (passwords strictly sanitized)
    logger = DeployLogger(project, environment, secrets_to_mask=[password] if password else None)
    logger.info(f"Initiating deployment: {project} -> {environment} ({protocol.upper()}) [ZipMode={is_zip_mode}]")

    # Resolve local path
    local_path_str = env_cfg.get("local_path", ".")
    local_dir = (local_path_override or Path(local_path_str)).resolve()

    if not local_dir.exists():
        console.print(f"[bold red]Error:[/bold red] Local path does not exist: {local_dir}")
        logger.error(f"Local path not found: {local_dir}")
        return False

    # 4. Instantiate Provider
    try:
        provider = get_provider(
            protocol=protocol,
            host=host,
            username=username,
            password=password,
            port=port,
            key_path=key_path,
            remote_path=remote_path,
        )
    except Exception as e:
        console.print(f"[bold red]Provider error:[/bold red] {str(e)}")
        logger.error(f"Provider error: {str(e)}")
        return False

    # 5. Scan Local Files and Compute Diff
    exclude_patterns = env_cfg.get("exclude", [])
    local_files = scan_local_files(local_dir, exclude_patterns)

    cached_state = None if remote_scan else load_deployment_state(project, environment)

    if cached_state and "files" in cached_state:
        # Instant diff using local state cache (~0.02s)
        remote_files = cached_state.get("files", {})
        diff = compute_diff(local_files, remote_files, detect_deletions=True, exclude_patterns=exclude_patterns)
    else:
        remote_files = {}
        scanned_via_bridge = False

        # Try fast PHP manifest scanner if app_url is configured (0.3s instead of 60s)
        if effective_app_url:
            with console.status(f"[bold cyan]Fast remote scanning via PHP bridge ({effective_app_url})..."):
                token = secrets.token_hex(16)
                scan_script_name = f"_deployctl_scan_{token[:8]}.php"
                temp_scan_file = Path(tempfile.gettempdir()) / scan_script_name
                try:
                    php_code = generate_php_scan_script(token)
                    temp_scan_file.write_text(php_code, encoding="utf-8")

                    provider.connect()
                    provider.upload_file(temp_scan_file, scan_script_name)

                    scan_ok, scan_msg, scan_files = fetch_remote_manifest(
                        effective_app_url,
                        scan_script_name,
                        token,
                        server_ip=host,
                    )

                    try:
                        provider.delete_file(scan_script_name)
                    except Exception:
                        pass

                    if scan_ok and isinstance(scan_files, dict) and len(scan_files) > 0:
                        remote_files = scan_files
                        scanned_via_bridge = True
                        console.print(f"[dim]✔ Fast remote scan complete: {len(remote_files)} files retrieved via PHP Bridge.[/dim]")
                except Exception:
                    scanned_via_bridge = False
                finally:
                    if temp_scan_file.exists():
                        try:
                            temp_scan_file.unlink()
                        except Exception:
                            pass

        if not scanned_via_bridge:
            # Fallback to standard sequential FTP traversal
            with console.status(f"[bold cyan]Scanning remote files via {protocol.upper()}..."):
                try:
                    provider.connect()
                    remote_files = provider.list_remote()
                except Exception as e:
                    provider.close()
                    console.print(f"[bold red]Connection failed:[/bold red] {str(e)}")
                    logger.error(f"Connection failed: {str(e)}")
                    return False

        diff = compute_diff(local_files, remote_files, detect_deletions=True, exclude_patterns=exclude_patterns)

    # 6. Dry Run Check
    if dry_run:
        diff_text = format_diff_text(diff, is_dry_run=True, remote_path=remote_path)
        console.print(diff_text)
        if is_zip_mode:
            console.print(f"[dim]Strategy: Zip & Remote PHP Bridge (Target URL: {effective_app_url})[/dim]")
        provider.close()
        logger.info(f"Dry run complete. Target: {remote_path}. Added: {len(diff.added)}, Modified: {len(diff.modified)}, Deleted: {len(diff.deleted)}")
        return True

    # Check if there are changes to deploy
    if not diff.has_changes:
        console.print(f"[green]✔ Everything is up to date.[/green] Working tree matches remote ({remote_path}). No upload needed.")
        provider.close()
        save_deployment_state(project, environment, local_files, metadata={"strategy": "zip" if is_zip_mode else "standard"})
        logger.finalize("SUCCESS_NO_CHANGES", 0, time.time() - start_time)
        return True

    # 7. Tier 3: Production Confirmation Safeguard
    is_prod = environment.lower() == "production"
    needs_confirm = is_prod and global_cfg.get("confirm_production", True) and not skip_confirm

    total_files = len(diff.upload_files)
    total_mb = round(diff.total_upload_bytes / (1024 * 1024), 2)

    if needs_confirm:
        mode_label = "Zip & PHP Extract" if is_zip_mode else "Direct File Sync"
        panel_content = (
            f"[bold yellow]⚠ Production deployment[/bold yellow]\n\n"
            f"Project    : [cyan]{project}[/cyan]\n"
            f"Server     : [magenta]{environment}[/magenta]\n"
            f"Protocol   : [blue]{protocol.upper()}[/blue]\n"
            f"Strategy   : [bold magenta]{mode_label}[/bold magenta]\n"
            f"Remote Path: [bold green]{remote_path}[/bold green]\n"
            f"Files      : [bold]{total_files}[/bold] (+{len(diff.added)} ~{len(diff.modified)})\n"
            f"Size       : [dim]{total_mb} MB[/dim]"
        )
        console.print(Panel(panel_content, border_style="yellow"))

        try:
            proceed = Confirm.ask("Continue?", default=False)
            if not proceed:
                console.print("[yellow]Deployment cancelled by user.[/yellow]")
                logger.warning("Deployment aborted at confirmation prompt.")
                provider.close()
                return False
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Deployment cancelled.[/yellow]")
            provider.close()
            return False

    # 8. Upload files: Mode A (Zip & PHP Bridge) or Mode B (Standard FTP)
    if is_zip_mode:
        console.print(f"\n[bold green]Deploying {total_files} files using Zip & PHP Auto-Extract Bridge...[/bold green]")
        logger.info(f"Packaging and uploading {total_files} files via Zip Bridge to {effective_app_url}")

        temp_dir = Path(tempfile.mkdtemp(prefix="deployctl_zip_"))
        token = secrets.token_hex(16)
        zip_name = f"_deployctl_pkg_{token[:8]}.zip"
        bridge_name = f"_deployctl_ext_{token[:8]}.php"
        local_zip = temp_dir / zip_name
        local_bridge = temp_dir / bridge_name

        try:
            with console.status(f"[bold cyan]Packaging {total_files} files into zip archive ({zip_name})..."):
                files_count, zip_size = create_deployment_zip(local_dir, diff.upload_files, local_zip)
                php_script = generate_php_bridge_script(token, zip_name)
                local_bridge.write_text(php_script, encoding="utf-8")

            zip_mb = round(zip_size / (1024 * 1024), 2)
            console.print(f"[cyan]Uploading {zip_name} ({zip_mb} MB) and extractor bridge...[/cyan]")

            # Upload zip archive with progress
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                zip_task = progress.add_task(f"[cyan]Uploading {zip_name}...", total=zip_size)

                def _zip_cb(chunk_bytes: int):
                    progress.update(zip_task, advance=chunk_bytes)

                provider.upload_file(local_zip, zip_name, callback=_zip_cb)

            # Upload bridge PHP script
            provider.upload_file(local_bridge, bridge_name)

            # Trigger remote extraction via HTTP
            with console.status(f"[bold cyan]Triggering remote PHP extraction at {effective_app_url}..."):
                extract_ok, extract_msg, extract_data = trigger_remote_extraction(
                    effective_app_url,
                    bridge_name,
                    token,
                    server_ip=host,
                )

            # Handle deletions if any
            for del_rel in diff.deleted:
                try:
                    provider.delete_file(del_rel)
                except Exception:
                    pass

            # Cleanup remote leftovers just in case
            try:
                provider.delete_file(zip_name)
                provider.delete_file(bridge_name)
            except Exception:
                pass

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            provider.close()

        duration = time.time() - start_time
        if not extract_ok:
            console.print(f"[bold red]✖ Remote extraction failed:[/bold red] {extract_msg}")
            console.print(f"[dim]Endpoint: {effective_app_url.rstrip('/')}/{bridge_name}[/dim]")
            logger.finalize("FAILED_ZIP_EXTRACT", 0, duration, {"error": extract_msg, "url": effective_app_url})
            return False

        console.print(f"\n[bold green]✔ Deployment successful (Zip & PHP Bridge)![/bold green] {total_files} files extracted in {duration:.1f}s.")
        save_deployment_state(project, environment, local_files, metadata={"strategy": "zip", "app_url": effective_app_url})
        logger.finalize("SUCCESS_ZIP", total_files, duration, {"zip_size": zip_size, "app_url": effective_app_url})
        return True

    # Standard file-by-file upload with progress bar
    console.print(f"\n[bold green]Deploying {total_files} files to {project}:{environment}...[/bold green]")
    logger.info(f"Uploading {total_files} files ({total_mb} MB)")

    success_count = 0
    failed_files: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task("[cyan]Uploading...", total=diff.total_upload_bytes)

        for rel_path in diff.upload_files:
            local_info = local_files[rel_path]
            file_task = progress.add_task(f"[dim]{rel_path[:35]}[/dim]", total=local_info.size)

            def _progress_cb(chunk_bytes: int):
                progress.update(overall_task, advance=chunk_bytes)
                progress.update(file_task, advance=chunk_bytes)

            try:
                provider.upload_file(local_info.abs_path, rel_path, callback=_progress_cb)
                success_count += 1
            except Exception as e:
                failed_files.append(rel_path)
                logger.error(f"Failed to upload {rel_path}: {str(e)}")
            finally:
                progress.remove_task(file_task)

    # Handle deleted files
    for del_rel in diff.deleted:
        try:
            provider.delete_file(del_rel)
        except Exception:
            pass

    provider.close()
    duration = time.time() - start_time

    # 9. Results Summary
    if failed_files:
        console.print(f"[bold red]Deployment finished with {len(failed_files)} errors.[/bold red]")
        for f in failed_files[:5]:
            console.print(f"  [red]✖ {f}[/red]")
        logger.finalize("FAILED_PARTIAL", success_count, duration, {"failed": failed_files})
        return False

    console.print(f"\n[bold green]✔ Deployment successful![/bold green] {success_count} files uploaded in {duration:.1f}s.")
    save_deployment_state(project, environment, local_files, metadata={"strategy": "standard"})
    logger.finalize("SUCCESS", success_count, duration, {"uploaded_bytes": diff.total_upload_bytes})
    return True
