"""Expose the local EduBridge web UI with a public HTTPS URL."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

CLOUDFLARED_URLS = {
    "win32": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
    "darwin": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64.tgz",
    "linux": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
}


def _binary_name() -> str:
    return "cloudflared.exe" if sys.platform == "win32" else "cloudflared"


def _candidate_paths(app_dir: Path) -> list[Path]:
    name = _binary_name()
    project_bin = Path(__file__).resolve().parent / "bin" / name
    return [project_bin, app_dir / name]


def ensure_cloudflared(app_dir: Path) -> Path:
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    candidates = _candidate_paths(app_dir)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 5_000_000:
            return path
    dest = candidates[0]
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = CLOUDFLARED_URLS.get(sys.platform, CLOUDFLARED_URLS["linux"])
    print(f"Downloading a public tunnel helper from Cloudflare...\n{url}", flush=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=60) as source, partial.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if partial.stat().st_size < 5_000_000:
        partial.unlink(missing_ok=True)
        raise OSError("The public tunnel helper download was incomplete.")
    partial.replace(dest)
    if sys.platform != "win32":
        dest.chmod(0o755)
    return dest


def start_public_tunnel(local_port: int, app_dir: Path, on_url) -> subprocess.Popen:
    binary = ensure_cloudflared(app_dir)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    process = subprocess.Popen(
        [str(binary), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{local_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=creationflags,
    )

    def watch() -> None:
        url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        assert process.stdout is not None
        tail: list[str] = []
        for line in process.stdout:
            tail.append(line.rstrip())
            del tail[:-8]
            match = url_re.search(line)
            if match:
                on_url(match.group(0))
        code = process.wait()
        process.tunnel_note = " ".join(tail) or f"tunnel helper exited ({code})"  # type: ignore[attr-defined]

    threading.Thread(target=watch, daemon=True).start()
    return process


def wait_for_url(holder: dict, process: subprocess.Popen | None = None, timeout: float = 90.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if holder.get("url"):
            return holder["url"]
        if process is not None and process.poll() is not None:
            time.sleep(0.3)
            if holder.get("url"):
                return holder["url"]
            note = getattr(process, "tunnel_note", "")
            raise TimeoutError(note or "The public tunnel helper stopped before a URL was ready.")
        time.sleep(0.2)
    raise TimeoutError("Could not create a public URL. Check internet access, then try again.")
