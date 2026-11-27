"""Let a phone next to this Windows computer open EduBridge."""
from __future__ import annotations

import subprocess
import sys

HOTSPOT_SSID = "EduBridge"
HOTSPOT_PASSWORD = "edubridge"
HOTSPOT_HOST = "192.168.137.1"

_START = r"""
$ErrorActionPreference = "Stop"
$net = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
$ops = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
$profile = $net::GetInternetConnectionProfile()
if ($null -eq $profile) { throw "This computer has no internet connection to share." }
$manager = $ops::CreateFromConnectionProfile($profile)
$cfg = $manager.GetCurrentAccessPointConfiguration()
if ($manager.TetheringOperationalState -eq "On" -and $cfg.Ssid -eq "EduBridge") { Write-Output "on"; exit 0 }
$cfg.Ssid = "EduBridge"
$cfg.Passphrase = "edubridge"
$null = $manager.ConfigureAccessPointAsync($cfg)
Start-Sleep -Seconds 1
if ($manager.TetheringOperationalState -ne "On") {
  $null = $manager.StartTetheringAsync()
  Start-Sleep -Seconds 3
}
if ($manager.TetheringOperationalState -ne "On") { throw "The EduBridge Wi-Fi did not start." }
Write-Output "on"
"""


def start_phone_hotspot(port: int) -> str:
    """Start the EduBridge Wi-Fi hotspot and return the address a phone should open."""
    if sys.platform != "win32":
        return ""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _START],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 or "on" not in completed.stdout:
        message = completed.stderr.strip() or completed.stdout.strip() or "The EduBridge Wi-Fi did not start."
        raise OSError(message.splitlines()[-1])
    if "on" not in output:
        raise OSError("The EduBridge Wi-Fi did not start.")
    return f"http://{HOTSPOT_HOST}:{port}"
