#!/usr/bin/env python3
"""Submit one real macOS notification from launchd, without invoking Codex.

This verifies submission, not whether Focus/notification settings show a banner.
"""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if sys.platform != "darwin":
    raise SystemExit("This integration test requires macOS and a GUI login.")

with tempfile.TemporaryDirectory(prefix="codex-heartbeat-notification-") as temporary:
    base = Path(temporary)
    label = "local.codex-heartbeat.notification-test-" + uuid.uuid4().hex
    domain = f"gui/{os.getuid()}"
    service = domain + "/" + label
    path = base / "notification.plist"
    path.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": ["/bin/bash", str(ROOT / "bin/heartbeatctl"), "test-notification"],
        "RunAtLoad": True,
        "EnvironmentVariables": {"HEARTBEAT_LOG_DIR": str(base / "logs")},
        "StandardOutPath": str(base / "stdout"),
        "StandardErrorPath": str(base / "stderr"),
    }))
    loaded = False
    try:
        subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], check=True)
        loaded = True
        log = base / "logs/heartbeat.log"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if log.exists() and "notification submitted\n" in log.read_text():
                print("PASS: launchd submitted a real macOS notification; no Codex request.")
                print("Banner visibility depends on macOS Notifications and Focus settings.")
                break
            if log.exists() and "notification submission failed" in log.read_text():
                raise AssertionError(log.read_text())
            time.sleep(0.1)
        else:
            raise AssertionError("Notification test timed out: " + (base / "stderr").read_text())
    finally:
        if loaded:
            subprocess.run(["/bin/launchctl", "bootout", service], check=True)
