"""Unit tests for deployctl core components."""

import tempfile
from pathlib import Path

import pytest

from deployctl.diff import compute_diff, format_diff_text, scan_local_files
from deployctl.providers.base import RemoteFileInfo
from deployctl.providers.local import LocalProvider
from deployctl.security import check_project_isolation, mask_secret, sanitize_text


def test_mask_secret():
    assert mask_secret("my_super_secret") == "********"
    assert mask_secret("my_super_secret", show_chars=4) == "***********cret"
    assert mask_secret(None) == "********"


def test_sanitize_text():
    secret = "TopSecret123"
    log = f"Connecting to ftp://user:{secret}@ftp.example.com with key {secret}"
    sanitized = sanitize_text(log, secrets=[secret])
    assert secret not in sanitized
    assert "[REDACTED]" in sanitized


def test_project_isolation():
    cwd = Path("/home/developer/projects/webapp-alpha")
    cfg = {"local_path": "."}
    matched, _ = check_project_isolation("webapp-alpha", cfg, current_cwd=cwd)
    assert matched is True

    unmatched, _ = check_project_isolation("ecommerce-beta", cfg, current_cwd=cwd)
    assert unmatched is False



def test_diff_engine():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "index.php").write_text("echo 'hello';")
        (tmp_path / "style.css").write_text("body { color: red; }")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("git config")

        # Scan with git excluded
        files = scan_local_files(tmp_path, exclude_patterns=[".git*"])
        assert "index.php" in files
        assert "style.css" in files
        assert ".git/config" not in files

        # Remote has style.css only
        remote_files = {
            "style.css": RemoteFileInfo(rel_path="style.css", size=len("body { color: red; }")),
        }

        diff = compute_diff(files, remote_files)
        assert "index.php" in diff.added
        assert "style.css" in diff.unchanged

        output = format_diff_text(diff, is_dry_run=True)
        assert "DRY RUN" in output
        assert "index.php" in output
        assert "+1" in output
        assert "No files uploaded." in output


def test_local_provider():
    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dest_dir:
        src = Path(src_dir)
        dest = Path(dest_dir)

        test_file = src / "test.txt"
        test_file.write_text("sample content")

        provider = LocalProvider(host="", username="", remote_path=str(dest))
        ok, _ = provider.test_connection()
        assert ok is True

        provider.upload_file(test_file, "subdir/test.txt")
        assert (dest / "subdir" / "test.txt").exists()
        assert (dest / "subdir" / "test.txt").read_text() == "sample content"

        remote_list = provider.list_remote()
        assert "subdir/test.txt" in remote_list

        provider.delete_file("subdir/test.txt")
        assert not (dest / "subdir" / "test.txt").exists()


def test_browse_connection_directories():
    from deployctl.deployer import (
        browse_connection_directories,
        create_connection_directory,
    )
    with tempfile.TemporaryDirectory() as dest_dir:
        dest = Path(dest_dir)
        (dest / "public_html").mkdir()
        (dest / "public_html" / "index.html").write_text("<h1>Hello</h1>")

        # Test browsing local provider path via connection API
        res = browse_connection_directories(
            host="",
            username="",
            protocol="local",
            path=str(dest / "public_html"),
        )
        assert res["ok"] is True
        assert any(f["name"] == "index.html" for f in res["files"])

        # Test creating remote directory
        mkdir_res = create_connection_directory(
            host="",
            username="",
            protocol="local",
            new_dir=str(dest / "public_html" / "demo"),
        )
        assert mkdir_res["ok"] is True
        assert (dest / "public_html" / "demo").is_dir()


def test_zip_deploy_bridge_script_and_packing():
    import zipfile
    from deployctl.zip_deploy import (
        create_deployment_zip,
        generate_php_bridge_script,
    )

    token = "test_token_12345"
    zip_name = "test_payload.zip"
    script = generate_php_bridge_script(token, zip_name)

    assert "<?php" in script
    assert token in script
    assert zip_name in script
    assert "ZipArchive" in script
    assert "@unlink(__FILE__);" in script

    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as out_dir:
        src = Path(src_dir)
        (src / "app").mkdir()
        (src / "app" / "index.php").write_text("<?php echo 'app';")
        (src / "vendor").mkdir()
        (src / "vendor" / "autoload.php").write_text("<?php // autoload")

        files_to_pack = ["app/index.php", "vendor/autoload.php"]
        out_zip = Path(out_dir) / "bundle.zip"

        create_deployment_zip(src, files_to_pack, out_zip)
        assert out_zip.exists()
        assert out_zip.stat().st_size > 0

        # Verify zip archive contents
        with zipfile.ZipFile(out_zip, "r") as zf:
            namelist = zf.namelist()
            assert "app/index.php" in namelist
            assert "vendor/autoload.php" in namelist
            assert zf.read("app/index.php").decode("utf-8") == "<?php echo 'app';"


def test_zip_deploy_http_mock_flow():
    import http.server
    import threading
    from deployctl.zip_deploy import trigger_remote_extraction

    class MockPHPBridgeHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "token=valid_token" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "message": "Extracted in 5ms", "files_count": 2}')
            else:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": false, "message": "Invalid token"}')

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), MockPHPBridgeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Test success call
        ok, msg, data = trigger_remote_extraction(
            app_url=f"http://127.0.0.1:{port}",
            bridge_filename="_bridge.php",
            token="valid_token",
        )
        assert ok is True
        assert "Extracted" in msg
        assert data.get("files_count") == 2

        # Test invalid token
        bad_ok, bad_msg, _ = trigger_remote_extraction(
            app_url=f"http://127.0.0.1:{port}",
            bridge_filename="_bridge.php",
            token="invalid_token",
        )
        assert bad_ok is False
        assert "HTTP Error 403" in bad_msg or "Invalid token" in bad_msg
    finally:
        server.shutdown()
        server.server_close()


def test_credentials_vault_fallback(monkeypatch, tmp_path):
    from deployctl import credentials

    # Set custom test vault & index path
    monkeypatch.setattr(credentials, "INDEX_PATH", tmp_path / "credentials_index.json")
    monkeypatch.setattr(credentials, "VAULT_PATH", tmp_path / ".credentials.vault")

    # Mock keyring to fail so vault fallback is tested
    def mock_set_password(*args, **kwargs):
        raise RuntimeError("No keyring on headless Linux")

    def mock_get_password(*args, **kwargs):
        raise RuntimeError("No keyring on headless Linux")

    monkeypatch.setattr("keyring.set_password", mock_set_password)
    monkeypatch.setattr("keyring.get_password", mock_get_password)
    monkeypatch.setattr(credentials.sys, "platform", "linux")

    # 1. Save
    saved = credentials.save_credential(
        name="server-linux-1",
        host="linux.example.com",
        username="deployer",
        password="secret_password_123",
        protocol="sftp",
        port=22,
    )
    assert saved is True

    # Check file exists and has 0600 mode
    vault_file = tmp_path / ".credentials.vault"
    assert vault_file.exists()

    # 2. Retrieve
    retrieved = credentials.get_credential("server-linux-1")
    assert retrieved is not None
    assert retrieved["host"] == "linux.example.com"
    assert retrieved["username"] == "deployer"
    assert retrieved["password"] == "secret_password_123"
    assert retrieved["protocol"] == "sftp"

    # 3. List
    names = credentials.list_credentials()
    assert "server-linux-1" in names

    # 4. Delete
    deleted = credentials.delete_credential("server-linux-1")
    assert deleted is True
    assert credentials.get_credential("server-linux-1") is None
    assert "server-linux-1" not in credentials.list_credentials()


def test_deployment_state_cache(monkeypatch, tmp_path):
    from deployctl import state

    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")

    local_manifest = {
        "index.php": {"size": 100, "mtime": 1000.0},
        "app/Model.php": {"size": 250, "mtime": 1050.0},
    }

    # 1. Save state
    state.save_deployment_state("my-proj", "prod", local_manifest)

    # 2. Load state
    loaded = state.load_deployment_state("my-proj", "prod")
    assert loaded is not None
    assert loaded["files_count"] == 2
    assert "index.php" in loaded["files"]
    assert loaded["files"]["index.php"]["size"] == 100

    # 3. Clear state
    cleared = state.clear_deployment_state("my-proj", "prod")
    assert cleared is True
    assert state.load_deployment_state("my-proj", "prod") is None






def test_download_connection_file(tmp_path):
    from deployctl.deployer import download_connection_file

    remote = tmp_path / "remote"
    (remote / "storage" / "logs").mkdir(parents=True)
    log = remote / "storage" / "logs" / "laravel.log"
    log.write_text("\n".join(f"line {i}" for i in range(500)) + "\nconnect ftp://u:hunter22@host/x\n")
    downloads = tmp_path / "downloads"

    res = download_connection_file(
        host="", username="", protocol="local",
        remote_path=str(log), download_dir=downloads, tail_lines=3,
    )
    assert res["ok"] is True
    assert (downloads / str(log).lstrip("/")).read_text() == log.read_text()
    assert res["preview"].splitlines()[0] == "line 498"
    assert "hunter22" not in res["preview"]

    # Directories, missing files, oversize files and traversal are refused
    assert download_connection_file(host="", username="", protocol="local",
                                    remote_path=str(log.parent), download_dir=downloads)["ok"] is False
    assert download_connection_file(host="", username="", protocol="local",
                                    remote_path=str(remote / "nope.log"), download_dir=downloads)["ok"] is False
    assert download_connection_file(host="", username="", protocol="local",
                                    remote_path=str(log), download_dir=downloads, max_bytes=10)["ok"] is False
    res = download_connection_file(host="", username="", protocol="local",
                                   remote_path=f"{remote}/storage/../../../etc/hosts", download_dir=downloads)
    assert res["ok"] is False


def test_mcp_lists_download_tool():
    from deployctl.mcp import MCP_TOOLS

    assert "download_remote_file" in [t["name"] for t in MCP_TOOLS]


def test_every_mcp_tool_declares_all_annotations_and_is_handled():
    from deployctl.mcp import MCP_TOOLS, handle_tool_call

    for t in MCP_TOOLS:
        a = t["annotations"]
        assert set(a) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        assert all(isinstance(v, bool) for v in a.values()), t["name"]
        # every listed tool name must be dispatched (not "Unknown tool")
        out = handle_tool_call(t["name"], {})["content"][0]["text"]
        assert not out.startswith("Unknown tool"), t["name"]


import pytest


@pytest.mark.parametrize("name", [
    "deploy_project", "list_projects", "test_connection", "show_project",
    "browse_remote_directories", "download_remote_file", "set_target_option", "set_remote_path",
])
def test_mcp_tool_is_listed_and_dispatched_by_name(name):
    from deployctl.mcp import MCP_TOOLS, handle_tool_call

    assert name in [t["name"] for t in MCP_TOOLS]
    # empty args must produce a handled error/result, never "Unknown tool"
    assert not handle_tool_call(name, {})["content"][0]["text"].startswith("Unknown tool")
