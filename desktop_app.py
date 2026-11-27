"""Open EduBridge in its own window, like a desktop app."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WINDOW_WIDTH = 460
WINDOW_HEIGHT = 640

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8080"


def _browser() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _server_is_up() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=0.4) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _port_is_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", 8080)) == 0


def _pythonw() -> str:
    bundled = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if bundled.is_file():
        return str(bundled)
    return "pythonw"


def _start_server() -> None:
    if _port_is_open():
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [_pythonw(), str(ROOT / "edubridge.py"), "--no-browser"],
        cwd=ROOT,
        creationflags=flags,
        close_fds=True,
    )


def _fit_saved_window(profile: Path) -> None:
    """Edge restores the last window, including a maximized one that leaves a blank area."""
    prefs_path = profile / "Default" / "Preferences"
    if not prefs_path.is_file():
        return
    data = json.loads(prefs_path.read_text(encoding="utf-8"))

    def fit(node: object) -> None:
        if not isinstance(node, dict):
            return
        if "maximized" in node and "left" in node and "right" in node:
            node["maximized"] = False
            node["left"] = 80
            node["top"] = 20
            node["right"] = 80 + WINDOW_WIDTH
            node["bottom"] = 20 + WINDOW_HEIGHT
        for value in node.values():
            fit(value)

    fit(data.get("browser", {}).get("app_window_placement", {}))
    prefs_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _tell(message: str) -> None:
    ctypes = __import__("ctypes")
    ctypes.windll.user32.MessageBoxW(None, message, "EduBridge", 0x10)


def main() -> None:
    browser = _browser()
    if browser is None:
        _tell("Install Microsoft Edge or Google Chrome to open the EduBridge window.")
        return
    _start_server()
    for _ in range(40):
        if _server_is_up():
            break
        time.sleep(0.25)
    else:
        _tell("EduBridge did not open. Start it from run.bat to see the error.")
        return
    profile = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "EduBridge" / "app-window"
    profile.mkdir(parents=True, exist_ok=True)
    _fit_saved_window(profile)
    subprocess.Popen(
        [
            str(browser),
            f"--app={URL}",
            f"--user-data-dir={profile}",
            "--new-window",
            f"--window-size={WINDOW_WIDTH},{WINDOW_HEIGHT}",
            "--window-position=80,20",
            "--no-first-run",
            "--disable-extensions",
        ],
        close_fds=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _tell = globals().get("_tell")
        if _tell:
            _tell(str(exc))
        sys.exit(1)
