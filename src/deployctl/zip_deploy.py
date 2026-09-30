"""Zip & Remote PHP Auto-Extract Deployment Strategy for deployctl.

Packs files into a single zip archive, uploads via FTP/SFTP in seconds,
and extracts remotely on the hosting server via a temporary self-destructing PHP Bridge.
"""

from __future__ import annotations

import json
import os
import secrets
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable


def generate_php_bridge_script(token: str, zip_filename: str) -> str:
    """Generate self-destructing PHP extraction script."""
    return f"""<?php
/**
 * deployctl PHP Extraction Bridge
 * Automatically generated and self-deleting deploy worker.
 */
header('Content-Type: application/json; charset=utf-8');

// Suppress raw error output to keep JSON response clean
error_reporting(0);
@ini_set('display_errors', '0');
@set_time_limit(300);

$expectedToken = '{token}';
$zipFilename = '{zip_filename}';

$providedToken = isset($_GET['token']) ? $_GET['token'] : (isset($_POST['token']) ? $_POST['token'] : '');

if (empty($providedToken) || $providedToken !== $expectedToken) {{
    http_response_code(403);
    echo json_encode(['ok' => false, 'message' => 'Invalid or missing authentication token.']);
    exit(1);
}}

$startTime = microtime(true);
$zipPath = __DIR__ . '/' . $zipFilename;

if (!file_exists($zipPath)) {{
    http_response_code(404);
    echo json_encode(['ok' => false, 'message' => 'Zip payload not found: ' . $zipFilename]);
    @unlink(__FILE__);
    exit(1);
}}

$extractedCount = 0;
$extractSuccess = false;
$errorMsg = '';

// 1. Try PHP ZipArchive first
if (class_exists('ZipArchive')) {{
    $zip = new ZipArchive();
    $res = $zip->open($zipPath);
    if ($res === TRUE) {{
        $extractedCount = $zip->numFiles;
        $extractSuccess = $zip->extractTo(__DIR__);
        $zip->close();
        if (!$extractSuccess) {{
            $errorMsg = 'ZipArchive::extractTo failed';
        }}
    }} else {{
        $errorMsg = 'ZipArchive failed to open zip file, code: ' . $res;
    }}
}}

// 2. Fallback to exec('unzip ...') if ZipArchive fails or is unavailable
if (!$extractSuccess && function_exists('exec')) {{
    $cmd = 'unzip -o ' . escapeshellarg($zipPath) . ' -d ' . escapeshellarg(__DIR__) . ' 2>&1';
    @exec($cmd, $output, $returnVar);
    if ($returnVar === 0) {{
        $extractSuccess = true;
    }} else {{
        $errorMsg .= ' | exec(unzip) failed: ' . implode(' ', (array)$output);
    }}
}}

// Delete the zip payload
@unlink($zipPath);

// Self-destruct bridge script
@unlink(__FILE__);

$duration = round((microtime(true) - $startTime) * 1000, 2);

if ($extractSuccess) {{
    echo json_encode([
        'ok' => true,
        'message' => 'Successfully extracted payload in ' . $duration . 'ms',
        'files_count' => $extractedCount,
        'duration_ms' => $duration,
    ]);
}} else {{
    http_response_code(500);
    echo json_encode([
        'ok' => false,
        'message' => 'Extraction failed: ' . $errorMsg,
        'duration_ms' => $duration,
    ]);
}}
"""


def create_deployment_zip(
    local_dir: Path,
    files_to_pack: list[str],
    output_zip_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Compress files into a single zip archive.
    
    Returns (total_files_count, total_zip_bytes).
    """
    local_dir = local_dir.resolve()
    output_zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        total = len(files_to_pack)
        for idx, rel_path in enumerate(files_to_pack, 1):
            full_path = local_dir / rel_path
            if full_path.is_file():
                zf.write(full_path, arcname=rel_path)
            if progress_cb:
                progress_cb(idx, total)

    zip_size = output_zip_path.stat().st_size
    return len(files_to_pack), zip_size


def _build_smart_request(app_url: str, filename: str, token: str, user_agent: str, server_ip: str | None = None) -> urllib.request.Request:
    clean_url = app_url.rstrip("/")
    target_url = f"{clean_url}/{filename}"
    params = urllib.parse.urlencode({"token": token})
    full_url = f"{target_url}?{params}"

    parsed = urllib.parse.urlparse(full_url)
    original_host = parsed.netloc

    # If server_ip is provided and hostname differs from server_ip, prepare fallback endpoint
    headers = {
        "User-Agent": user_agent,
        "Host": original_host,
    }

    # If domain fails DNS or if requested directly, allow targeting server_ip
    return urllib.request.Request(full_url, headers=headers)


def trigger_remote_extraction(
    app_url: str,
    bridge_filename: str,
    token: str,
    timeout: int = 60,
    server_ip: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Send HTTP request to trigger the PHP extraction bridge on the hosting server."""
    clean_url = app_url.rstrip("/")
    target_url = f"{clean_url}/{bridge_filename}"
    params = urllib.parse.urlencode({"token": token})
    full_url = f"{target_url}?{params}"

    parsed = urllib.parse.urlparse(full_url)
    original_host = parsed.netloc

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Support self-signed SSL on development/staging domains

    # Build primary and fallback candidate URLs
    candidate_urls = [full_url]
    if server_ip and parsed.hostname != server_ip:
        port_part = f":{parsed.port}" if parsed.port else ""
        ip_netloc = f"{server_ip}{port_part}"
        ip_url = parsed._replace(netloc=ip_netloc).geturl()
        candidate_urls.append(ip_url)

    last_error = ""
    for candidate in candidate_urls:
        req = urllib.request.Request(
            candidate,
            headers={
                "User-Agent": "deployctl/0.1.0 (Zip-Bridge-AutoExtractor)",
                "Host": original_host,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                status_code = response.getcode()
                raw_body = response.read().decode("utf-8", errors="replace")

                try:
                    data = json.loads(raw_body)
                except Exception:
                    data = {"raw": raw_body}

                if status_code == 200 and data.get("ok"):
                    return True, data.get("message", "Extraction completed successfully"), data
                else:
                    return False, data.get("message", f"Extraction failed with HTTP {status_code}: {raw_body[:200]}"), data
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_error = f"HTTP Error {e.code}: {err_body[:200]}"
        except Exception as e:
            last_error = str(e)

    return False, f"Failed to connect to extraction endpoint ({target_url}): {last_error}", {}


def generate_php_scan_script(token: str) -> str:
    """Generate self-destructing PHP remote manifest scanner script."""
    return f"""<?php
/**
 * deployctl Fast Remote Manifest Scanner Bridge
 * Scans remote directory locally on the server in 0.05s instead of sequential FTP roundtrips.
 */
header('Content-Type: application/json; charset=utf-8');
error_reporting(0);
@ini_set('display_errors', '0');
@set_time_limit(300);

$expectedToken = '{token}';
$providedToken = isset($_GET['token']) ? $_GET['token'] : (isset($_POST['token']) ? $_POST['token'] : '');

if (empty($providedToken) || $providedToken !== $expectedToken) {{
    http_response_code(403);
    echo json_encode(['ok' => false, 'message' => 'Invalid or missing authentication token.']);
    exit(1);
}}

$startTime = microtime(true);
$baseDir = realpath(__DIR__);
$files = [];

register_shutdown_function(function() {{
    @unlink(__FILE__);
}});

try {{
    $dirIter = new RecursiveDirectoryIterator($baseDir, RecursiveDirectoryIterator::SKIP_DOTS);
    $iter = new RecursiveIteratorIterator($dirIter, RecursiveIteratorIterator::SELF_FIRST);

    foreach ($iter as $item) {{
        if ($item->isFile()) {{
            $fullPath = $item->getPathname();
            if ($fullPath === __FILE__) {{
                continue;
            }}
            $relPath = substr($fullPath, strlen($baseDir) + 1);
            $relPath = str_replace('\\\\', '/', $relPath);
            $files[$relPath] = [
                'size' => $item->getSize(),
                'mtime' => $item->getMTime(),
            ];
        }}
    }}

    $duration = round((microtime(true) - $startTime) * 1000, 2);

    echo json_encode([
        'ok' => true,
        'duration_ms' => $duration,
        'count' => count($files),
        'files' => $files,
    ]);
}} catch (Exception $e) {{
    http_response_code(500);
    echo json_encode([
        'ok' => false,
        'message' => $e->getMessage(),
    ]);
}}
@unlink(__FILE__);
"""


def fetch_remote_manifest(
    app_url: str,
    scan_filename: str,
    token: str,
    timeout: int = 60,
    server_ip: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Fetch remote filesystem manifest from the PHP scanner bridge in a single HTTP request."""
    clean_url = app_url.rstrip("/")
    target_url = f"{clean_url}/{scan_filename}"

    params = urllib.parse.urlencode({"token": token})
    full_url = f"{target_url}?{params}"

    parsed = urllib.parse.urlparse(full_url)
    original_host = parsed.netloc

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    # Build primary and fallback candidate URLs
    candidate_urls = [full_url]
    if server_ip and parsed.hostname != server_ip:
        port_part = f":{parsed.port}" if parsed.port else ""
        ip_netloc = f"{server_ip}{port_part}"
        ip_url = parsed._replace(netloc=ip_netloc).geturl()
        candidate_urls.append(ip_url)

    last_error = ""
    for candidate in candidate_urls:
        req = urllib.request.Request(
            candidate,
            headers={
                "User-Agent": "deployctl/0.1.0 (Remote-Manifest-Scanner)",
                "Host": original_host,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                status_code = response.getcode()
                raw_body = response.read().decode("utf-8", errors="replace")

                try:
                    data = json.loads(raw_body)
                except Exception:
                    data = {"raw": raw_body}

                if status_code == 200 and data.get("ok"):
                    return True, f"Scanned {data.get('count', 0)} files in {data.get('duration_ms', 0)}ms", data.get("files", {})
                else:
                    return False, data.get("message", f"Scan failed with HTTP {status_code}"), {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_error = f"HTTP Error {e.code}: {err_body[:200]}"
        except Exception as e:
            last_error = str(e)

    return False, f"Failed to connect to scanner endpoint ({target_url}): {last_error}", {}

