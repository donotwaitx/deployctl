"""Tests for the deployment safeguards: git policy, structured report, failed deletes, locking, TLS, MCP."""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from deployctl import deployer
from deployctl.diff import is_excluded
from deployctl.gitinfo import evaluate_git_policy, get_git_context
from deployctl.lock import deploy_lock
from deployctl.report import MAX_LISTED_FILES, DeployReport
from deployctl.state import get_state_file, load_deployment_state


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def target(tmp_path, monkeypatch):
    """A `local`-protocol target: nothing touches the keychain, the network or ~/.deployctl state."""
    import deployctl.lock as lock_mod
    import deployctl.logger as logger_mod
    import deployctl.state as state_mod

    local = tmp_path / "src"
    local.mkdir()
    (local / "a.txt").write_text("a")
    remote = tmp_path / "remote"
    remote.mkdir()

    env = {
        "protocol": "local",
        "credential": "c",
        "remote_path": str(remote),
        "local_path": str(local),
        "exclude": [],
    }
    global_cfg = {"confirm_production": True, "state_max_age_days": 7}
    monkeypatch.setattr(deployer, "get_project_target", lambda p, e: {**env, "project": p, "environment": e})
    monkeypatch.setattr(deployer, "get_credential", lambda name: {"host": "", "username": "", "password": None})
    monkeypatch.setattr(deployer, "load_global_config", lambda: global_cfg)
    monkeypatch.setattr(state_mod, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(lock_mod, "LOCKS_DIR", tmp_path / "locks")
    monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path / "logs")

    class Target:
        pass

    t = Target()
    t.local, t.remote, t.env, t.global_cfg = local, remote, env, global_cfg
    return t


def _deploy(environment="demo", **kwargs):
    report = DeployReport()
    kwargs.setdefault("skip_confirm", True)
    ok = deployer.run_deployment("p", environment, report=report, **kwargs)
    return ok, report


# --- deployment flow -------------------------------------------------------------------------------------


def test_deploy_fills_report_and_second_run_is_up_to_date(target):
    (target.local / "b.txt").write_text("b")

    ok, report = _deploy()
    assert ok and report.status == "SUCCESS"
    assert sorted(report.added) == ["a.txt", "b.txt"] and report.uploaded == 2
    assert report.diff_source == "ftp_scan"
    assert (target.remote / "b.txt").read_text() == "b"

    ok, report = _deploy()
    assert ok and report.status == "UP_TO_DATE" and report.diff_source == "state_cache"


def test_dry_run_reports_the_diff_and_uploads_nothing(target):
    ok, report = _deploy(dry_run=True)
    assert ok and report.status == "DRY_RUN"
    assert report.added == ["a.txt"]
    assert not (target.remote / "a.txt").exists()
    assert report.to_dict()["files"]["added"] == ["a.txt"]


def test_failed_delete_is_reported_kept_in_state_and_retried(target, monkeypatch):
    (target.local / "b.txt").write_text("b")
    assert _deploy()[0]
    (target.local / "b.txt").unlink()

    from deployctl.providers.local import LocalProvider

    real_delete = LocalProvider.delete_file
    monkeypatch.setattr(LocalProvider, "delete_file", lambda self, rel: False)
    ok, report = _deploy()
    assert ok and report.status == "SUCCESS_WITH_WARNINGS"
    assert report.failed_deletes == ["b.txt"]
    assert (target.remote / "b.txt").exists()
    assert "b.txt" in load_deployment_state("p", "demo")["files"]

    monkeypatch.setattr(LocalProvider, "delete_file", real_delete)
    ok, report = _deploy()
    assert ok and report.deleted == ["b.txt"] and report.failed_deletes == []
    assert not (target.remote / "b.txt").exists()


def test_stale_state_cache_triggers_a_server_scan(target):
    assert _deploy()[0]
    state_file = get_state_file("p", "demo")
    data = json.loads(state_file.read_text())
    data["updated_at"] = time.time() - 30 * 86400
    state_file.write_text(json.dumps(data))

    ok, report = _deploy()
    assert ok and report.diff_source == "ftp_scan"
    assert any("30 days old" in w for w in report.warnings)


def test_remote_files_matching_exclude_patterns_are_not_deleted(target):
    target.env["exclude"] = ["storage/**"]
    (target.remote / "storage").mkdir()
    (target.remote / "storage" / "keep.log").write_text("x")

    ok, report = _deploy()
    assert ok and report.deleted == []
    assert (target.remote / "storage" / "keep.log").exists()


def test_post_deploy_delete_removes_matching_cache_files(target):
    target.env["exclude"] = ["bootstrap/cache/**"]
    target.env["post_deploy_delete"] = ["bootstrap/cache/*.php", "../escape.php", "/etc/*"]
    cache = target.remote / "bootstrap" / "cache"
    cache.mkdir(parents=True)
    (cache / "config.php").write_text("<?php")
    (cache / ".gitignore").write_text("*")
    (target.remote.parent / "escape.php").write_text("keep")

    ok, report = _deploy()
    assert ok and report.post_deploy_deleted == ["bootstrap/cache/config.php"]
    assert not (cache / "config.php").exists()
    assert (cache / ".gitignore").exists()
    assert (target.remote.parent / "escape.php").exists()


def test_production_needs_confirmation_when_not_interactive(target):
    ok, report = _deploy("production", skip_confirm=False, interactive=False)
    assert not ok and report.status == "CONFIRMATION_REQUIRED"
    assert report.added == ["a.txt"]  # the diff is there for review
    assert not (target.remote / "a.txt").exists()

    ok, report = _deploy("production", skip_confirm=True, interactive=False)
    assert ok and (target.remote / "a.txt").exists()


def test_concurrent_deployment_is_refused(target):
    with deploy_lock("p", "demo") as held:
        assert held
        ok, report = _deploy()
        assert not ok and report.status == "LOCKED"
    assert _deploy()[0]


# --- git safeguards --------------------------------------------------------------------------------------


@pytest.fixture
def repo(target):
    _git(target.local, "init", "-q", "-b", "main")
    _git(target.local, "add", "-A")
    _git(target.local, "commit", "-q", "-m", "init")
    return target.local


def test_git_context_reports_branch_commit_and_dirty_files(repo):
    git = get_git_context(repo)
    assert git["available"] and git["branch"] == "main" and git["dirty_files"] == 0 and git["commit"]

    (repo / "new.txt").write_text("x")
    assert get_git_context(repo)["dirty_files"] == 1


def test_git_context_outside_a_repo(tmp_path):
    assert get_git_context(tmp_path) == {"available": False}


def test_git_policy():
    git = {"available": True, "branch": "feature/x", "commit": "abc", "dirty_files": 2}

    warnings, blockers = evaluate_git_policy(git, {})
    assert blockers == [] and len(warnings) == 2

    warnings, blockers = evaluate_git_policy(git, {"allowed_branches": ["develop"], "require_clean": True})
    assert len(blockers) == 2

    previous = {"available": True, "branch": "develop", "commit": "def"}
    warnings, _ = evaluate_git_policy({**git, "branch": "main", "dirty_files": 0}, {}, previous)
    assert any("last deployment was from branch 'develop'" in w for w in warnings)


def test_branch_outside_allowed_list_blocks_deploy_unless_forced(target, repo):
    target.env["allowed_branches"] = ["develop"]

    ok, report = _deploy()
    assert not ok and report.status == "GIT_BLOCKED"
    assert not (target.remote / "a.txt").exists()

    ok, report = _deploy(dry_run=True)
    assert ok and any(w.startswith("Would be blocked") for w in report.warnings)

    ok, report = _deploy(force_branch=True)
    assert ok and (target.remote / "a.txt").exists()
    assert report.git["branch"] == "main"
    assert load_deployment_state("p", "demo")["metadata"]["git"]["branch"] == "main"


# --- report ----------------------------------------------------------------------------------------------


def test_report_caps_listed_files():
    report = DeployReport(added=[f"f{i}" for i in range(MAX_LISTED_FILES + 5)])
    data = report.to_dict()
    assert data["counts"]["added"] == MAX_LISTED_FILES + 5
    assert len(data["files"]["added"]) == MAX_LISTED_FILES and data["truncated"] is True


# --- exclude patterns, state, TLS, PHP --------------------------------------------------------------------


def test_directory_glob_does_not_match_sibling_with_same_prefix():
    assert is_excluded("storage/app/x.log", ["storage/**"])
    assert is_excluded("storage", ["storage/**"])
    assert not is_excluded("storage-backup/x.log", ["storage/**"])


def test_tls_is_verified_unless_the_target_opts_out():
    import ssl

    from deployctl.zip_deploy import _build_ssl_context, _candidate_urls

    assert _build_ssl_context(True).verify_mode == ssl.CERT_REQUIRED
    assert _build_ssl_context(False).verify_mode == ssl.CERT_NONE

    url = "https://example.com/x.php?token=t"
    assert _candidate_urls(url, "1.2.3.4", True) == [url]
    assert _candidate_urls(url, "1.2.3.4", False) == [url, "https://1.2.3.4/x.php?token=t"]


def test_php_bridges_compare_tokens_in_constant_time():
    from deployctl.zip_deploy import generate_php_bridge_script, generate_php_scan_script

    for script in (generate_php_bridge_script("tok", "p.zip"), generate_php_scan_script("tok")):
        assert "hash_equals" in script and "!== $expectedToken" not in script


# --- MCP -------------------------------------------------------------------------------------------------


def test_mcp_deploy_returns_the_report_and_requires_confirmation(target):
    from deployctl.mcp import handle_tool_call

    result = handle_tool_call("deploy_project", {"project": "p", "environment": "production"})
    report = json.loads(result["content"][0]["text"])
    assert result["isError"] is True and report["status"] == "CONFIRMATION_REQUIRED"
    assert report["files"]["added"] == ["a.txt"]

    result = handle_tool_call("deploy_project", {"project": "p", "environment": "production", "yes": True})
    report = json.loads(result["content"][0]["text"])
    assert result["isError"] is False and report["counts"]["uploaded"] == 1


def test_mcp_server_keeps_stdout_clean_json(target, monkeypatch, capsys):
    from deployctl.mcp import run_mcp_server

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "deploy_project", "arguments": {"project": "p", "environment": "demo"}}},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"))
    original_console = deployer.console
    try:
        run_mcp_server()
    finally:
        deployer.console = original_console

    out_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(out_lines) == 1  # only the JSON-RPC response; no progress output leaked into stdout
    assert json.loads(out_lines[0])["id"] == 1


def test_mcp_set_remote_path_edits_the_global_registry_not_a_local_override(tmp_path, monkeypatch):
    import deployctl.config as config
    import deployctl.mcp as mcp

    registry = tmp_path / "projects.yaml"
    registry.write_text("projects:\n  p:\n    demo:\n      remote_path: /old\n")
    monkeypatch.setattr(config, "PROJECTS_FILE", registry)
    monkeypatch.setattr(mcp, "PROJECTS_FILE", registry)
    monkeypatch.setattr(config, "DEPLOYCTL_HOME", tmp_path)
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")

    work = tmp_path / "work"
    work.mkdir()
    (work / ".deployctl.yaml").write_text("projects:\n  other:\n    prod:\n      remote_path: /x\n")
    monkeypatch.chdir(work)

    result = mcp.handle_tool_call("set_remote_path", {"project": "p", "environment": "demo", "remote_path": "/new"})
    assert result["isError"] is False
    text = registry.read_text()
    assert "/new" in text and "other" not in text
