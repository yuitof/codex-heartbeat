#!/usr/bin/env python3
"""Offline integration tests. No real Codex calls or system sleep."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/heartbeat.sh"
CTL = ROOT / "bin/heartbeatctl"

FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
if '--help' in sys.argv:
    print('--ephemeral --json --ignore-user-config --ignore-rules --sandbox')
    sys.exit(0)
with open(os.environ['FAKE_CALLS'], 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\n')
def emit(event): print(json.dumps(event), flush=True)
mode = os.environ.get('FAKE_MODE', 'success')
if mode == 'slow': time.sleep(1.5)
if mode == 'timeout':
    subprocess.Popen([sys.executable, '-c',
        "import time,pathlib; time.sleep(3); pathlib.Path(" + repr(os.environ['FAKE_ORPHAN']) + ").touch()"])
    time.sleep(30)
if mode == 'usage':
    emit({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit. Weekly limit reached."}})
    sys.exit(0)  # Deliberately misleading process status.
if mode == 'network':
    print('connection reset by peer', file=sys.stderr)
    sys.exit(1)
if mode == 'stderr-limit':
    print('HTTP 429 quota exceeded', file=sys.stderr)
if mode == 'empty': sys.exit(0)
if mode == 'malformed': print('OK'); sys.exit(0)
if mode == 'tool':
    emit({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'touch unwanted'}})
if mode == 'error-with-ok': emit({'type': 'error', 'message': 'stream failed'})
emit({'type': 'thread.started', 'thread_id': 'test'})
emit({'type': 'turn.started'})
emit({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'NO' if mode == 'wrong' else 'OK'}})
if mode != 'incomplete': emit({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
sys.exit(1 if mode == 'nonzero-ok' else 0)
'''

FAKE_LAUNCHCTL = r'''#!/usr/bin/env python3
import os, pathlib, plistlib, sys
active = pathlib.Path(os.environ['FAKE_ACTIVE'])
active.mkdir(exist_ok=True)
with open(os.environ['FAKE_LAUNCH_CALLS'], 'a') as f: f.write(' '.join(sys.argv[1:]) + '\n')
action = sys.argv[1]
if action == 'print': sys.exit(0 if (active / sys.argv[2].split('/')[-1]).exists() else 113)
if action == 'bootstrap':
    if os.environ.get('FAKE_BOOTSTRAP_FAIL'): sys.exit(5)
    label = plistlib.loads(pathlib.Path(sys.argv[3]).read_bytes())['Label']
    (active / label).touch()
if action == 'bootout': (active / sys.argv[2].split('/')[-1]).unlink(missing_ok=True)
'''


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-heartbeat-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / "state"
        self.state.mkdir()
        self.logs = self.base / "logs"
        self.calls = self.base / "calls"
        self.fake = self.base / "fake codex & cli"
        self.fake.write_text(FAKE_CODEX)
        self.fake.chmod(0o700)
        self.launch = self.base / "launchctl"
        self.launch.write_text(FAKE_LAUNCHCTL)
        self.launch.chmod(0o700)
        self.env = dict(os.environ, HEARTBEAT_STATE_DIR=str(self.state),
                        HEARTBEAT_LOG_DIR=str(self.logs), HEARTBEAT_CODEX_BIN=str(self.fake),
                        FAKE_CALLS=str(self.calls), FAKE_ORPHAN=str(self.base / "orphan"),
                        HEARTBEAT_BASE_DIR=str(self.base / "install & path"),
                        HEARTBEAT_AGENTS_DIR=str(self.base / "agents"),
                        HEARTBEAT_LAUNCHCTL=str(self.launch),
                        FAKE_ACTIVE=str(self.base / "active"),
                        FAKE_LAUNCH_CALLS=str(self.base / "launch-calls"))

    def run_script(self, mode="success", script=SCRIPT, args=(), expected=0, **extra):
        result = subprocess.run(["/bin/bash", str(script), *args],
                                env=dict(self.env, FAKE_MODE=mode, **extra),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def stamp(self, name, value):
        (self.state / name).write_text(str(value) + "\n")

    def read_stamp(self, name):
        return int((self.state / name).read_text())

    def count(self):
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def install_and_arm(self):
        self.run_script(script=CTL, args=("install",))
        self.arm()

    def arm(self, **extra):
        return self.run_script(script=Path(self.env["HEARTBEAT_BASE_DIR"]) / "bin/schedule.sh", **extra)

    def worker_plist(self):
        return plistlib.loads((Path(self.env["HEARTBEAT_AGENTS_DIR"]) / "local.codex-heartbeat.plist").read_bytes())

    def test_first_success_and_safe_arguments(self):
        before = int(time.time())
        self.run_script()
        self.assertGreaterEqual(self.read_stamp("last-success"), before)
        self.assertEqual(self.count(), 1)
        args = json.loads(self.calls.read_text())
        for value in ("exec", "--ephemeral", "--json", "--ignore-user-config", "--ignore-rules", "read-only", "forced_login_method=\"chatgpt\"", "shell_tool", "hooks", "plugins"):
            self.assertIn(value, args)
        self.assertEqual(args[-1], "Reply with exactly OK. Do not use tools.")
        self.assertFalse(list(self.state.glob("run.*")))
        self.run_script()
        self.assertEqual(self.count(), 1)

    def test_not_due_and_status_never_invoke_codex(self):
        self.stamp("last-success", int(time.time()) - 18100)
        self.run_script()
        result = self.run_script(args=("status",))
        self.assertIn("due in", result.stdout)
        self.assertEqual(self.count(), 0)

    def test_due_after_302_minutes(self):
        self.stamp("last-success", int(time.time()) - 18121)
        self.run_script()
        self.assertEqual(self.count(), 1)

    def test_elapsed_sleep_gap_simulation(self):
        # This tests elapsed-time logic only, NOT actual macOS sleep/wake.
        self.stamp("last-success", int(time.time()) - 3 * 86400)
        self.run_script()
        self.assertEqual(self.count(), 1)

    def test_failures_preserve_success_and_delay_retries(self):
        for mode, label in (("usage", "usage-limit"), ("network", "network-error"),
                            ("stderr-limit", "usage-limit"), ("empty", "invalid-response"),
                            ("malformed", "malformed-events"), ("incomplete", "invalid-response"),
                            ("tool", "unexpected-tool"), ("error-with-ok", "request-error"),
                            ("wrong", "invalid-response"), ("nonzero-ok", "request-error")):
            with self.subTest(mode=mode):
                old = int(time.time()) - 20000
                self.stamp("last-success", old)
                self.stamp("last-attempt", old)
                self.run_script(mode=mode, expected=1)
                self.assertEqual(self.read_stamp("last-success"), old)
                self.assertIn("failure=" + label, (self.logs / "heartbeat.log").read_text())
                count = self.count()
                self.run_script()
                self.assertEqual(self.count(), count)
                self.stamp("last-attempt", int(time.time()) - 901)
                self.run_script()
                self.assertEqual(self.count(), count + 1)

    def test_timeout_kills_descendants_and_releases_lock(self):
        self.run_script(mode="timeout", expected=1, HEARTBEAT_TIMEOUT_SECONDS="1")
        self.assertFalse((self.state / "last-success").exists())
        self.assertIn("failure=timeout", (self.logs / "heartbeat.log").read_text())
        time.sleep(3.1)
        self.assertFalse((self.base / "orphan").exists())
        self.stamp("last-attempt", int(time.time()) - 901)
        self.run_script()

    def test_duplicate_execution_prevention(self):
        env = dict(self.env, FAKE_MODE="slow")
        procs = [subprocess.Popen(["/bin/bash", str(SCRIPT)], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
        for proc in procs:
            out, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, (out, err))
        self.assertEqual(self.count(), 1)

    def test_lock_retained_when_parent_is_killed(self):
        proc = subprocess.Popen(["/bin/bash", str(SCRIPT)], env=dict(self.env, FAKE_MODE="slow"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            if self.count(): break
            time.sleep(0.02)
        self.assertEqual(self.count(), 1)
        proc.kill()
        proc.wait(timeout=5)
        # Reset backoff so only the lock can prevent a second model call.
        self.stamp("last-attempt", int(time.time()) - 1000)
        self.run_script()
        self.assertEqual(self.count(), 1)
        time.sleep(1.7)
        self.run_script()
        self.assertEqual(self.count(), 2)

    def test_corrupt_state_fails_closed(self):
        self.stamp("last-success", "1+$(touch should-not-exist)")
        self.run_script(expected=1)
        self.assertEqual(self.count(), 0)

    def test_future_timestamp_does_not_send(self):
        future = int(time.time()) + 10000
        self.stamp("last-success", future)
        self.run_script()
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.read_stamp("last-success"), future)

    def test_missing_binary_backs_off(self):
        self.run_script(expected=1, HEARTBEAT_CODEX_BIN="/nonexistent", PATH="/usr/bin:/bin")
        self.assertFalse((self.state / "last-success").exists())
        self.assertIn("executable-not-found", (self.logs / "heartbeat.log").read_text())
        self.run_script()
        self.assertEqual(self.count(), 0)

    def test_log_rotation(self):
        self.logs.mkdir()
        (self.logs / "heartbeat.log").write_text("x" * 1048577)
        self.run_script()
        self.assertTrue((self.logs / "heartbeat.log.1").exists())
        self.assertLess((self.logs / "heartbeat.log").stat().st_size, 1024)

    def test_idempotent_install_plist_and_uninstall(self):
        success = int(time.time())
        self.stamp("last-success", success)
        for _ in range(2): self.install_and_arm()
        plist = Path(self.env["HEARTBEAT_AGENTS_DIR"]) / "local.codex-heartbeat.plist"
        data = plistlib.loads(plist.read_bytes())
        self.assertEqual(set(data["StartCalendarInterval"]), {"Month", "Day", "Hour", "Minute"})
        self.assertFalse(data["RunAtLoad"])
        self.assertNotIn("KeepAlive", data)
        self.assertEqual(data["EnvironmentVariables"]["HEARTBEAT_STATE_DIR"], str(self.state))
        self.assertIn("install & path", data["ProgramArguments"][1])
        calls = Path(self.env["FAKE_LAUNCH_CALLS"]).read_text().splitlines()
        self.assertEqual(sum(c.startswith("bootstrap ") for c in calls), 2)
        self.assertFalse(any(c.startswith("bootout ") for c in calls))
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.read_stamp("last-success"), success)
        for _ in range(2): self.run_script(script=CTL, args=("uninstall",))
        self.assertFalse(plist.exists())
        self.assertFalse(plist.with_name("local.codex-heartbeat.scheduler.plist").exists())
        self.assertEqual(self.read_stamp("last-success"), success)

    def test_success_arms_only_next_heartbeat(self):
        self.install_and_arm()
        self.assertTrue(self.worker_plist()["RunAtLoad"])
        self.run_script()
        self.assertTrue((self.state / "schedule-queue/pending").exists())
        self.arm()
        success = self.read_stamp("last-success")
        self.assertEqual(self.read_stamp("scheduled-for"), ((success + 18120 + 59) // 60) * 60)
        self.assertFalse(self.worker_plist()["RunAtLoad"])
        self.assertFalse((self.state / "schedule-queue/pending").exists())
        before = Path(self.env["FAKE_LAUNCH_CALLS"]).read_text().count("bootstrap ")
        self.arm()
        self.assertEqual(Path(self.env["FAKE_LAUNCH_CALLS"]).read_text().count("bootstrap "), before)
        self.assertEqual(self.count(), 1)

    def test_failure_arms_retry_then_success_moves_deadline(self):
        self.install_and_arm()
        self.run_script(mode="usage", expected=1)
        self.arm()
        attempt = self.read_stamp("last-attempt")
        self.assertEqual(self.read_stamp("scheduled-for"), ((attempt + 900 + 59) // 60) * 60)
        self.assertFalse((self.state / "last-success").exists())
        self.stamp("last-attempt", int(time.time()) - 901)
        self.run_script()
        self.arm()
        success = self.read_stamp("last-success")
        self.assertEqual(self.read_stamp("scheduled-for"), ((success + 18120 + 59) // 60) * 60)

    def test_scheduler_reload_failure_preserves_request(self):
        self.stamp("last-success", int(time.time()))
        self.run_script(script=CTL, args=("install",))
        self.arm(expected=1, FAKE_BOOTSTRAP_FAIL="1")
        self.assertTrue((self.state / "schedule-queue/pending").exists())
        self.arm()
        self.assertFalse((self.state / "schedule-queue/pending").exists())
        self.assertEqual(self.count(), 0)

    def test_scheduler_calendar_handles_year_boundary(self):
        # No model request; scheduled timestamp is deliberately far in future.
        import datetime
        success = int(datetime.datetime(2030, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp())
        self.stamp("last-success", success)
        self.run_script(script=CTL, args=("install",))
        self.arm(TZ="UTC")
        self.assertEqual(self.worker_plist()["StartCalendarInterval"],
                         {"Month": 1, "Day": 1, "Hour": 5, "Minute": 2})

    def test_uninstall_disables_late_scheduler(self):
        self.stamp("last-success", int(time.time()))
        self.install_and_arm()
        self.run_script(script=CTL, args=("uninstall",))
        self.arm()
        self.assertFalse((Path(self.env["HEARTBEAT_AGENTS_DIR"]) / "local.codex-heartbeat.plist").exists())

    def test_scheduler_waits_for_active_heartbeat(self):
        self.install_and_arm()
        proc = subprocess.Popen(["/bin/bash", str(SCRIPT)], env=dict(self.env, FAKE_MODE="slow"))
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            if self.count(): break
            time.sleep(0.02)
        self.assertEqual(self.count(), 1)
        old = self.read_stamp("scheduled-for")
        scheduler = subprocess.Popen(["/bin/bash", str(Path(self.env["HEARTBEAT_BASE_DIR"]) / "bin/schedule.sh")], env=self.env)
        self.addCleanup(lambda: scheduler.poll() is None and scheduler.kill())
        time.sleep(0.2)
        self.assertIsNone(scheduler.poll())
        self.assertEqual(self.read_stamp("scheduled-for"), old)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertEqual(scheduler.wait(timeout=10), 0)
        self.assertGreater(self.read_stamp("scheduled-for"), int(time.time()) + 18000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
