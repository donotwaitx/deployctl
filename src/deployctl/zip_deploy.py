"""Zip & Remote PHP Auto-Extract Deployment Strategy for deployctl.

Packs files into a single zip archive, uploads via FTP/SFTP in seconds,
and extracts remotely on the hosting server via a temporary self-destructing PHP Bridge.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from deployctl.diff import HASH_MAX_BYTES


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

if (!is_string($providedToken) || $providedToken === '' || !hash_equals($expectedToken, $providedToken)) {{
    http_response_code(403);
    echo json_encode(['ok' => false, 'message' => 'Invalid or missing authentication token.']);
    exit(1);
}}

register_shutdown_function(function() {{
    @unlink(__FILE__);
}});

$startTime = microtime(true);
$zipPath = __DIR__ . '/' . $zipFilename;

if (!file_exists($zipPath)) {{
    http_response_code(404);
    echo json_encode(['ok' => false, 'message' => 'Zip payload not found: ' . $zipFilename]);
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
        if (!$extractSuccess) {{
            $lastErr = error_get_last();
            $errorMsg = 'ZipArchive::extractTo failed' . ($lastErr ? ': ' . $lastErr['message'] : '');
        }}
        $zip->close();
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

// Delete the zip payload on success
if ($extractSuccess) {{
    @unlink($zipPath);
}}

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


def _build_ssl_context(verify_tls: bool) -> ssl.SSLContext:
    """TLS context for talking to the bridge scripts.

    Certificates are verified unless the target opts out with `insecure_tls: true` (self-signed staging hosts).
    """
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _candidate_urls(full_url: str, server_ip: str | None) -> list[str]:
    """Candidates to call: direct server IP with Host header first, then canonical domain URL.

    Calling server IP directly bypasses Cloudflare/WAF, proxy timeouts (524), and DNS latency.
    """
    candidates = []
    parsed = urllib.parse.urlparse(full_url)
    if server_ip and parsed.hostname != server_ip:
        port_part = f":{parsed.port}" if parsed.port else ""
        candidates.append(parsed._replace(netloc=f"{server_ip}{port_part}").geturl())
    candidates.append(full_url)
    return candidates


def resolve_canonical_app_url(url: str, verify_tls: bool = True, timeout: int = 5) -> str:
    """Follow HTTP 301/302 redirects to find the canonical app URL (e.g. non-www -> www, http -> https)."""
    clean_url = url.rstrip("/")
    if not clean_url.startswith(("http://", "https://")):
        clean_url = f"https://{clean_url}"
    try:
        ctx = _build_ssl_context(verify_tls)
        req = urllib.request.Request(clean_url, headers={"User-Agent": "deployctl/0.2.0 (AutoDetect)"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            parsed = urllib.parse.urlparse(resp.geturl().rstrip("/"))
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return clean_url


def infer_app_url(remote_path: str, host: str, verify_tls: bool = True) -> str | None:
    """Infer candidate domain and subpath from remote_path (/domains/{domain}/public_html[/{subpath}]) or hostname."""
    m = re.search(r"/(?:domains|www)/([^/]+)/(?:public_html|public)(?:/(.+))?", remote_path)
    if m:
        domain = m.group(1)
        subpath = m.group(2)
        base = resolve_canonical_app_url(f"https://{domain}", verify_tls=verify_tls)
        return f"{base}/{subpath.strip('/')}" if subpath else base

    m = re.search(r"/(?:domains|www)/([^/]+)", remote_path)
    domain = m.group(1) if m else None

    m_pub = re.search(r"/(?:public_html|public)(?:/(.+))?", remote_path)
    subpath = m_pub.group(1) if m_pub else None

    if not domain and host and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        domain = host.split(":")[0]

    if domain:
        base = resolve_canonical_app_url(f"https://{domain}", verify_tls=verify_tls)
        return f"{base}/{subpath.strip('/')}" if subpath else base

    return None


def trigger_remote_extraction(
    app_url: str,
    bridge_filename: str,
    token: str,
    timeout: int = 60,
    server_ip: str | None = None,
    verify_tls: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Send HTTP request to trigger the PHP extraction bridge on the hosting server."""
    clean_url = app_url.rstrip("/")
    target_url = f"{clean_url}/{bridge_filename}"
    params = urllib.parse.urlencode({"token": token})
    full_url = f"{target_url}?{params}"

    parsed = urllib.parse.urlparse(full_url)
    original_host = parsed.netloc

    ctx = _build_ssl_context(verify_tls)
    candidate_urls = _candidate_urls(full_url, server_ip)

    last_error = ""
    for candidate in candidate_urls:
        headers = {
            "User-Agent": "deployctl/0.2.0 (Zip-Bridge-AutoExtractor)",
        }
        # Only set Host header for IP fallback candidates (where hostname != server_ip),
        # never for standard URLs — explicit Host breaks redirects (e.g. non-www -> www) into infinite loops.
        if candidate != full_url:
            headers["Host"] = original_host
            req_ctx = _build_ssl_context(verify_tls=False)
        else:
            req_ctx = ctx

        req = urllib.request.Request(candidate, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=req_ctx) as response:
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


def generate_php_scan_script(token: str, hash_max_bytes: int = HASH_MAX_BYTES) -> str:
    """Generate self-destructing PHP remote manifest scanner script.

    Files up to `hash_max_bytes` are listed with their `sha1`, so the diff can compare content.
    """
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

if (!is_string($providedToken) || $providedToken === '' || !hash_equals($expectedToken, $providedToken)) {{
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
    // CATCH_GET_CHILD skips unreadable directories instead of aborting the whole scan
    $iter = new RecursiveIteratorIterator($dirIter, RecursiveIteratorIterator::SELF_FIRST, RecursiveIteratorIterator::CATCH_GET_CHILD);

    foreach ($iter as $item) {{
        try {{
            // Symlinks (e.g. Laravel's public/storage) may point outside open_basedir; they are not deployed files
            if ($item->isLink()) {{
                continue;
            }}
            if ($item->isFile()) {{
                $fullPath = $item->getPathname();
                if ($fullPath === __FILE__) {{
                    continue;
                }}
                $relPath = substr($fullPath, strlen($baseDir) + 1);
                $relPath = str_replace('\\\\', '/', $relPath);
                $size = $item->getSize();
                $entry = [
                    'size' => $size,
                    'mtime' => $item->getMTime(),
                ];
                if ($size <= {hash_max_bytes}) {{
                    $hash = sha1_file($fullPath);
                    if (is_string($hash)) {{
                        $entry['sha1'] = $hash;
                    }}
                }}
                $files[$relPath] = $entry;
            }}
        }} catch (Throwable $e) {{
            continue;
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
    verify_tls: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Fetch remote filesystem manifest from the PHP scanner bridge in a single HTTP request."""
    clean_url = app_url.rstrip("/")
    target_url = f"{clean_url}/{scan_filename}"

    params = urllib.parse.urlencode({"token": token})
    full_url = f"{target_url}?{params}"

    parsed = urllib.parse.urlparse(full_url)
    original_host = parsed.netloc

    ctx = _build_ssl_context(verify_tls)
    candidate_urls = _candidate_urls(full_url, server_ip)

    last_error = ""
    for candidate in candidate_urls:
        headers = {
            "User-Agent": "deployctl/0.2.0 (Remote-Manifest-Scanner)",
        }
        if candidate != full_url:
            headers["Host"] = original_host
            req_ctx = _build_ssl_context(verify_tls=False)
        else:
            req_ctx = ctx

        req = urllib.request.Request(candidate, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=timeout, context=req_ctx) as response:
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

