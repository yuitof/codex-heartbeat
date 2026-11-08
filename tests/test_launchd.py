#!/usr/bin/env python3
"""Real event-driven launchd integration with a fake Codex; no physical sleep."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

from test_heartbeat import FAKE_CODEX, ROOT

if sys.platform != "darwin":
    raise SystemExit("This integration test requires macOS and a GUI login.")

def wait_for(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(0.2)
    raise AssertionError("Timed out waiting for launchd state")

with tempfile.TemporaryDirectory(prefix="codex-heartbeat-launchd-") as temporary:
    base = Path(temporary)
    state, logs, agents = base / "state", base / "logs", base / "agents"
    state.mkdir()
    calls, mode = base / "calls", base / "mode"
    mode.write_text("usage")
    fake = base / "fake-codex"
    # Encode test paths in this fake executable, without adding test-specific
    # environment variables to production plists.
    fake.write_text(FAKE_CODEX.replace("import json, os, subprocess, sys, time",
        "import json, os, subprocess, sys, time\nfrom pathlib import Path\n"
        + "os.environ['FAKE_CALLS'] = " + repr(str(calls)) + "\n"
        + "os.environ['FAKE_MODE'] = Path(" + repr(str(mode)) + ").read_text()"))
    fake.chmod(0o700)
    label = "local.codex-heartbeat.test-" + uuid.uuid4().hex
    env = dict(os.environ, HEARTBEAT_BASE_DIR=str(base / "installed"),
               HEARTBEAT_STATE_DIR=str(state), HEARTBEAT_LOG_DIR=str(logs),
               HEARTBEAT_AGENTS_DIR=str(agents), HEARTBEAT_CODEX_BIN=str(fake),
               HEARTBEAT_LABEL=label)
    env.pop("HEARTBEAT_LAUNCHCTL", None)
    ctl = ["/bin/bash", str(ROOT / "bin/heartbeatctl")]
    worker = agents / (label + ".plist")
    target = ((int(time.time()) + 20 + 59) // 60) * 60
    previous_success = target - 18120
    (state / "last-success").write_text(str(previous_success))
    try:
        subprocess.run(ctl + ["install"], env=env, check=True)
        wait_for(worker.exists)
        config = plistlib.loads(worker.read_bytes())
        assert set(config["StartCalendarInterval"]) == {"Month", "Day", "Hour", "Minute"}
        assert not config["RunAtLoad"]
        assert int((state / "scheduled-for").read_text()) == target
        assert not calls.exists(), "Scheduler invoked model before due"
        print("PASS: specific next calendar time armed; no immediate model call", flush=True)
        print("Waiting for the dated calendar firing (up to 90 seconds)...", flush=True)
        wait_for(lambda: (state / "last-attempt").exists(), 90)
        wait_for(lambda: (state / "scheduled-for").exists()
                 and int((state / "scheduled-for").read_text()) >= int(time.time()) + 800)
        assert len(calls.read_text().splitlines()) == 1
        assert int((state / "last-success").read_text()) == previous_success
        attempt = int((state / "last-attempt").read_text())
        assert int((state / "scheduled-for").read_text()) == ((attempt + 900 + 59) // 60) * 60
        print("PASS: calendar invoked fake Codex; usage failure armed a 15-minute retry", flush=True)

        # Simulate the retry deadline having elapsed, then notify the scheduler.
        mode.write_text("success")
        (state / "last-attempt").write_text(str(int(time.time()) - 901))
        (state / "schedule-queue/pending").touch()
        wait_for(lambda: int((state / "last-success").read_text()) > previous_success)
        wait_for(lambda: int((state / "scheduled-for").read_text()) > int(time.time()) + 18000)
        success = int((state / "last-success").read_text())
        assert int((state / "scheduled-for").read_text()) == ((success + 18120 + 59) // 60) * 60
        assert len(calls.read_text().splitlines()) == 2
        assert not (state / "schedule-queue/pending").exists()
        assert not plistlib.loads(worker.read_bytes())["RunAtLoad"]
        print("PASS: overdue retry ran immediately; success armed the next 302-minute heartbeat", flush=True)
        print("Observing 65 seconds of idle scheduling to detect minute polling...", flush=True)
        service = f"gui/{os.getuid()}/{label}"
        def run_count():
            import re
            output = subprocess.check_output(["/bin/launchctl", "print", service], text=True)
            match = re.search(r"\bruns = (\d+)", output)
            return int(match.group(1)) if match else 0
        before = run_count()
        time.sleep(65)
        assert run_count() == before
        assert len(calls.read_text().splitlines()) == 2
        print("PASS: no periodic worker execution during the idle minute", flush=True)
        print("Physical sleep/wake and restart persistence were NOT tested.", flush=True)
    finally:
        subprocess.run(ctl + ["uninstall"], env=env, check=True)
