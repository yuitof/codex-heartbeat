# Codex heartbeat for macOS

Sends one minimal Codex message when **5 hours 2 minutes (18,120 seconds)** have
passed since the last successful heartbeat. This is intended to start a fresh
usage window after the previous one expires, including after idle periods.
The operating system launches it at the next due time. There is no minute polling
or process sleeping between heartbeats.

The script cannot detect whether the actual usage countdown is stopped. Its
timestamps approximate the window's timing; other Codex sessions can start a
window independently. This does not guarantee continuous windows or additional
quota. A heartbeat still consumes a small amount of usage, including CLI context.

## Install and operate

Requires macOS, a GUI login, a current Codex CLI, and existing **ChatGPT account**
authentication (`codex login`). The heartbeat forces ChatGPT authentication so
it does not silently switch to API billing. Runtime dependencies are macOS's
Bash, Perl and standard utilities; Python 3 is needed only for tests.

From this directory:

```bash
./bin/heartbeatctl install
./bin/heartbeatctl status
./bin/heartbeatctl logs
./bin/heartbeatctl test-notification
./bin/heartbeatctl uninstall
```

Installation discovers `codex` from `PATH`, validates the required CLI flags,
copies the implementation into your Application Support directory, and loads
two user LaunchAgents: `local.codex-heartbeat.scheduler.plist` and a dated
`local.codex-heartbeat.plist`. The scheduler calculates the next execution from
local timestamps. If already due or there is no prior success, it launches the
heartbeat immediately. Reinstalling preserves timestamps and does not restart
unchanged loaded jobs.
Run install again after moving Codex or changing its installation environment.

You can also run a manual eligibility check (it respects both interval and retry
delay), or inspect launchd itself:

```bash
./bin/heartbeatctl check
launchctl print "gui/$(id -u)/local.codex-heartbeat"
launchctl print "gui/$(id -u)/local.codex-heartbeat.scheduler"
tail -n 50 "$HOME/Library/Logs/CodexHeartbeat/heartbeat.log"
tail -n 50 "$HOME/Library/Logs/CodexHeartbeat/launchd.log"
```

Installed management commands remain usable if you move this source directory:

```bash
"$HOME/Library/Application Support/CodexHeartbeat/bin/heartbeatctl" status
"$HOME/Library/Application Support/CodexHeartbeat/bin/heartbeatctl" uninstall
```

Uninstall unloads both agents and removes their plists. It retains scripts, timestamps,
the lock inode and logs, making subsequent installation safe. No `sudo` is needed.
To remove retained files completely, first uninstall and ensure there is no
running heartbeat or child process, then remove these two directories:

```text
~/Library/Application Support/CodexHeartbeat
~/Library/Logs/CodexHeartbeat
```

## Behavior and safeguards

The heartbeat's `StartCalendarInterval` specifies the next month, day, hour and
minute. The due timestamp is rounded **up** to minute resolution, adding at most
59 seconds without sending early. After success the next due timestamp is
completion + 18,120 seconds; after failure it is completion + 900 seconds.

The separate scheduler runs at login (`RunAtLoad`) and when an attempt leaves a
request in its `QueueDirectories` directory. It waits on the heartbeat's lock,
then replaces the finished heartbeat's calendar job with the next date. This
avoids reloading a running job from within itself. Queue entries survive a killed
heartbeat or scheduler, so an interrupted attempt can be re-armed. If scheduling
fails, launchd retries the queued scheduler with a 10-second throttle; this
recovery work never invokes Codex. Between deadlines both agents normally have
no running process.

`StartCalendarInterval` coalesces missed executions into one run on wake, as described
by [Apple's scheduling documentation](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)
and the installed `man launchd.plist`. A due heartbeat can run after waking;
it does not begin another five-hour waiting period. A user LaunchAgent runs while
the user is logged in and loads again at login after a restart. It does not run
before login, wake the Mac, or keep it awake. Scheduling and network availability
can delay execution.

Persistent files live under
`~/Library/Application Support/CodexHeartbeat/state/`:

| File | Purpose |
| --- | --- |
| `last-success` | Unix time at the completion of the last validated success |
| `last-attempt` | Initially written before execution, then updated on completion |
| `heartbeat.lock` | Kernel advisory lock; never deleted by the program |
| `scheduled-for` | Actual calendar launch time, rounded up to a minute |
| `enabled` | Allows the scheduler to arm jobs; removed before uninstall |
| `schedule-queue/` | Persistent scheduling request, consumed after the attempt exits |
| `last-failure.jsonl`, `last-failure.stderr` | Latest failure diagnostics, capped at 64 KiB each |

When due, the script starts `codex --no-daemon exec` with `--ephemeral`, `--json`,
`--sandbox read-only`, `--skip-git-repo-check`, and an empty temporary working
directory. It ignores user configuration and rules, disables shell tools, hooks,
apps, plugins, memory and other integrations, disables web search, and requests
low reasoning effort. It does not change your Codex configuration, resume other
sessions, or operate inside a Git repository.
These options are documented in the official
[CLI reference](https://developers.openai.com/codex/cli/reference/),
[non-interactive guide](https://developers.openai.com/codex/noninteractive/) and
[configuration reference](https://developers.openai.com/codex/config-reference/).

The only prompt is:

```text
Reply with exactly OK. Do not use tools.
```

Success requires exit code zero, exactly one `turn.completed`, and exactly one
completed assistant message containing `OK`, with no error events or tool items.
Plain `OK` output, malformed JSON, partial streams, unexpected tools and zero-exit
usage-limit errors all fail validation. Only success updates `last-success`.

Failures schedule a retry at least **900 seconds after completion**, rounded up
to the next minute.
Five-hour and weekly limit errors are labeled `usage-limit`; detected connection
errors are labeled `network-error`. Other errors also retry without advancing
success. A request is bounded to 120 seconds; its supervisor terminates its own
process group on timeout. An interrupted attempt waits at least 900 seconds from
its recorded start. Nothing queries `/status`, quota, or the actual reset time.

The Perl helper uses kernel `flock` and passes the open lock descriptor to Bash
and its descendants. Multiple scheduled or manual invocations cannot overlap;
the kernel releases the lock when the final holder exits, including after a
crash. Clock adjustments backwards defer requests conservatively, and invalid
state fails closed. The heartbeat log rotates at 1 MiB and retains one backup.
Launchd's separate diagnostic log is normally empty; it is not rotated here.

## Notifications

Each heartbeat attempt sends a local macOS Notification Center notification
titled **Codex Heartbeat**, showing Codex's reply (`OK` on success) or its error
on failure. The subtitle shows success and the next interval, or failure and the
15-minute retry. Local eligibility checks, status commands and scheduling work
stay silent. No external push service or extra model request is used.

Notifications use macOS's built-in `osascript` and
[AppleScript `display notification`](https://developer.apple.com/library/archive/documentation/LanguagesUtilities/Conceptual/MacAutomationScriptingGuide/DisplayNotifications.html).
Reply text is passed as arguments rather than executable script, and is shortened
to 500 characters. If there is no reply or structured error, the notification
uses the last diagnostic line or a message saying no reply was received.
Notification submission is limited to 10 seconds; failure is logged and does not
change heartbeat success, timestamps or retries.

To check notifications without calling Codex:

```bash
./bin/heartbeatctl test-notification
```

If banners do not appear, check **System Settings → Notifications** for the script
or Script Editor entry and check Focus settings. macOS can accept a notification
without displaying a banner. The heartbeat log records submission, which does
not confirm that a banner was visible.

## Verification

```bash
bash -n bin/heartbeat.sh bin/heartbeatctl
perl -c lib/heartbeat.pl
python3 tests/test_heartbeat.py
python3 tests/test_launchd.py
python3 tests/test_notifications_macos.py
```

The offline suite uses a fake Codex and temporary state to check timing,
failure/retry handling, response validation, timeouts and child termination,
concurrent execution, parent death, logging, and idempotent installation.
The launchd test registers a uniquely labeled temporary agent, verifies an actual
dated calendar firing, a 15-minute failure retry schedule, immediate recovery of
an overdue retry, the subsequent success schedule, and an idle minute with no
worker executions. It then unloads both agents and invokes only the fake Codex.
It takes a few minutes and requires permission to register user LaunchAgents.
The notification integration test submits one real test notification from a
temporary launchd job without invoking Codex, then removes the test job. The
offline suite replaces `osascript` with a fake to avoid notifications during tests.

See [TESTING.md](TESTING.md) for the observed results and remaining manual checks.

For physical sleep/wake validation on this Mac, with the production agent loaded:

1. Choose a time shortly before the local next-eligible timestamp printed by
   `./bin/heartbeatctl status`. Record that timestamp and the current log tail.
2. Put the Mac to sleep and leave it asleep across that timestamp and at least
   one full calendar minute after the scheduled launch shown by status.
3. Wake it, then inspect `heartbeat.log` and `pmset -g log` to correlate the wake
   time with the attempt. Confirm that a due check happens promptly, without an
   additional five-hour delay. A network error must preserve the success timestamp
   and retry 15 minutes later.
4. After a later restart and login, use `heartbeatctl status` and `launchctl print`
   to confirm it loaded and preserved its timestamps.

These are manual acceptance procedures; a simulated elapsed-time gap is not proof
of physical sleep/wake behavior.

For isolated testing, the scripts accept `HEARTBEAT_STATE_DIR`,
`HEARTBEAT_LOG_DIR`, `HEARTBEAT_CODEX_BIN`, `HEARTBEAT_TIMEOUT_SECONDS`,
`HEARTBEAT_OSASCRIPT` (notification test executable), and the
installer additionally accepts `HEARTBEAT_BASE_DIR`, `HEARTBEAT_AGENTS_DIR`,
`HEARTBEAT_LAUNCHCTL` and `HEARTBEAT_LABEL`. Normal installation requires none of
these overrides. Custom base, state, agent paths and labels are carried through
both generated plists.

## License

This project is licensed under the [MIT License](LICENSE).
