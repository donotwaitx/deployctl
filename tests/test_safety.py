"""Tests for the deployment safeguards: git policy, structured report, failed deletes, locking, TLS, MCP."""

from __future__ import annotations

import io
import json
import os
import shutil
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
    assert _candidate_urls(url, "1.2.3.4") == ["https://1.2.3.4/x.php?token=t", url]
    assert _candidate_urls(url, None) == [url]


def test_php_bridges_compare_tokens_in_constant_time():
    from deployctl.zip_deploy import generate_php_bridge_script, generate_php_scan_script

    for script in (generate_php_bridge_script("tok", "p.zip"), generate_php_scan_script("tok")):
        assert "hash_equals" in script and "!== $expectedToken" not in script
        assert "register_shutdown_function" in script


def test_php_bridge_extract_captures_error_details():
    from deployctl.zip_deploy import generate_php_bridge_script

    script = generate_php_bridge_script("tok", "p.zip")
    assert "error_get_last()" in script


def test_infer_app_url_from_remote_path_or_host():
    from deployctl.zip_deploy import infer_app_url

    # Unreachable or non-redirecting domains fall back to the inferred clean https URL
    assert infer_app_url("/domains/local-test.internal/public_html", "1.2.3.4") == "https://local-test.internal"
    assert infer_app_url("/domains/local-test.internal/public_html/demo", "1.2.3.4") == "https://local-test.internal/demo"
    assert infer_app_url("/public_html/sub", "api.internal.local") == "https://api.internal.local/sub"
    assert infer_app_url("/public_html", "api.internal.local") == "https://api.internal.local"
    assert infer_app_url("/public_html", "192.168.1.1") is None


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


def test_php_scan_bridge_skips_symlinks_and_unreadable_entries():
    from deployctl.zip_deploy import generate_php_scan_script

    script = generate_php_scan_script("tok")
    assert "isLink()" in script and "CATCH_GET_CHILD" in script and "catch (Throwable" in script
    # the PHP source must still hold the Windows path separator replacement it had before
    assert "str_replace('\\\\', '/'" in script


# --- content hashes --------------------------------------------------------------------------------------


def test_diff_compares_content_when_both_sides_have_a_hash(tmp_path):
    import hashlib

    from deployctl.diff import LocalFileInfo, compute_diff, file_sha1

    f = tmp_path / "a.php"
    f.write_text("aaaa")
    info = LocalFileInfo("a.php", f, 4, f.stat().st_mtime)
    sha = lambda data: hashlib.sha1(data).hexdigest()  # noqa: E731

    # newer local mtime, same content: unchanged (this is what git checkout does to every file)
    old_mtime = f.stat().st_mtime - 3600
    diff = compute_diff({"a.php": info}, {"a.php": {"size": 4, "mtime": old_mtime, "sha1": sha(b"aaaa")}})
    assert diff.unchanged == ["a.php"] and not diff.modified

    # same size and mtime, different content: modified
    diff = compute_diff({"a.php": info}, {"a.php": {"size": 4, "mtime": f.stat().st_mtime, "sha1": sha(b"bbbb")}})
    assert diff.modified == ["a.php"]

    # no remote hash: the old size/mtime behaviour is unchanged
    diff = compute_diff({"a.php": info}, {"a.php": {"size": 4, "mtime": old_mtime}})
    assert diff.modified == ["a.php"]

    # files over the hash limit are never hashed
    big = LocalFileInfo("big.bin", f, 10**9, f.stat().st_mtime)
    assert file_sha1(big) is None


def test_state_cache_keeps_hashes_so_a_touched_file_is_not_redeployed(target):
    assert _deploy()[0]
    assert load_deployment_state("p", "demo")["files"]["a.txt"]["sha1"]

    # rewrite with identical content and a newer mtime, as a branch switch does
    path = target.local / "a.txt"
    path.write_text("a")
    new_time = time.time() + 60
    os.utime(path, (new_time, new_time))
    ok, report = _deploy()
    assert ok and report.status == "UP_TO_DATE"

    # same byte count, different content: detected
    path.write_text("b")
    ok, report = _deploy()
    assert ok and report.modified == ["a.txt"] and (target.remote / "a.txt").read_text() == "b"


def test_ftp_scan_warns_that_it_compares_sizes_only(target):
    ok, report = _deploy()
    assert any("sizes only" in w for w in report.warnings)


@pytest.mark.skipif(shutil.which("php") is None, reason="php not installed")
def test_php_scan_bridge_returns_sha1_for_small_files_only(tmp_path):
    import hashlib

    from deployctl.zip_deploy import generate_php_scan_script

    site = tmp_path / "site"
    (site / "app").mkdir(parents=True)
    (site / "app" / "small.txt").write_text("hello")
    (site / "big.bin").write_bytes(b"x" * 50)
    (site / "scan.php").write_text(generate_php_scan_script("tok", hash_max_bytes=10))

    out = subprocess.run(
        ["php", "-r", '$_GET["token"]="tok"; include "scan.php";'],
        cwd=site, capture_output=True, text=True, check=True,
    ).stdout
    files = json.loads(out)["files"]
    assert files["app/small.txt"]["sha1"] == hashlib.sha1(b"hello").hexdigest()
    assert "sha1" not in files["big.bin"]


# --- deletions -------------------------------------------------------------------------------------------


def _server_only_upload(target):
    """A file that exists only on the server, like an image uploaded through the remote admin."""
    (target.remote / "uploads").mkdir(exist_ok=True)
    path = target.remote / "uploads" / "from-admin.png"
    path.write_text("server only")
    return path


def test_server_only_files_are_protected_by_default(target):
    orphan = _server_only_upload(target)

    ok, report = _deploy(skip_confirm=False, interactive=False)
    assert ok and report.status == "SUCCESS"
    assert orphan.exists() and (target.remote / "a.txt").exists()
    assert report.skipped_deletes == ["uploads/from-admin.png"] and report.deleted == []
    assert report.to_dict()["skipped_deletes"] == ["uploads/from-admin.png"]
    assert any("did not deploy" in w for w in report.warnings)

    # a file deployctl deployed earlier and that was since removed locally may be deleted, but only after a yes
    (target.local / "b.txt").write_text("b")
    assert _deploy()[0]
    (target.local / "b.txt").unlink()

    ok, report = _deploy(skip_confirm=False, interactive=False)
    assert not ok and report.status == "DELETIONS_NEED_CONFIRMATION"
    assert report.deleted == ["b.txt"] and (target.remote / "b.txt").exists()

    ok, report = _deploy(skip_confirm=True, interactive=False)
    assert ok and not (target.remote / "b.txt").exists() and orphan.exists()


def test_delete_missing_all_removes_server_only_files_after_confirmation(target):
    target.env["delete_missing"] = "all"
    orphan = _server_only_upload(target)

    ok, report = _deploy(skip_confirm=False, interactive=False)
    assert not ok and report.status == "DELETIONS_NEED_CONFIRMATION"
    assert report.deleted == ["uploads/from-admin.png"]
    assert orphan.exists() and not (target.remote / "a.txt").exists()

    ok, report = _deploy(skip_confirm=True, interactive=False)
    assert ok and not orphan.exists()


def test_delete_missing_none_never_deletes_anything(target):
    target.env["delete_missing"] = False  # YAML `false` means none
    (target.local / "b.txt").write_text("b")
    assert _deploy()[0]
    (target.local / "b.txt").unlink()
    orphan = _server_only_upload(target)

    ok, report = _deploy(skip_confirm=False, interactive=False)
    assert ok and (target.remote / "b.txt").exists() and orphan.exists()
    assert report.skipped_deletes == ["b.txt"]  # the cache-based diff never even looks at server-only files


def test_normalize_delete_missing():
    from deployctl.config import normalize_delete_missing

    assert [normalize_delete_missing(v) for v in (None, True, False, "ALL", "none", "owned", "bogus", 3)] == [
        "owned", "all", "none", "all", "none", "owned", "owned", "owned",
    ]


def test_mcp_set_target_option_validates_and_edits_the_registry(tmp_path, monkeypatch):
    import deployctl.config as config
    import deployctl.mcp as mcp

    registry = tmp_path / "projects.yaml"
    registry.write_text("projects:\n  p:\n    demo:\n      remote_path: /x\n")
    monkeypatch.setattr(config, "PROJECTS_FILE", registry)
    monkeypatch.setattr(mcp, "PROJECTS_FILE", registry)
    monkeypatch.setattr(config, "DEPLOYCTL_HOME", tmp_path)
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")

    def call(**args):
        return mcp.handle_tool_call("set_target_option", {"project": "p", "environment": "demo", **args})

    assert call(option="delete_missing", value="ALL")["isError"] is False
    assert "delete_missing: all" in registry.read_text()
    assert call(option="allowed_branches", value=["develop", "main"])["isError"] is False

    assert call(option="delete_missing", value="sometimes")["isError"] is True
    assert call(option="remote_path", value="/etc")["isError"] is True  # not an allowed option
    assert call(option="require_clean", value="yes")["isError"] is True
    assert mcp.handle_tool_call("set_target_option", {"project": "nope", "option": "require_clean", "value": True})["isError"] is True

    assert call(option="delete_missing", value=None)["isError"] is False
    assert "delete_missing" not in registry.read_text() and "develop" in registry.read_text()
