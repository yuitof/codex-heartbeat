Implement an automated Codex CLI heartbeat system on macOS to keep the Codex five-hour usage window cycling.

### Requirements

1. **Five-hour interval**
   - Send a minimal prompt to Codex CLI (e.g., "Reply with exactly OK. Do not use tools.").
   - Record the Unix timestamp of the last successfully completed heartbeat.
   - Once 5 hours and 2 minutes have elapsed since that timestamp, send another heartbeat.
   - Do not use `/status` or query Codex's actual usage countdown. Use local timestamps only.

2. **Automatic scheduling**
   - Use macOS `launchd` to launch the heartbeat only when its next execution is due; do not poll every minute or keep a sleeping process running.
   - After success, schedule the next execution for 5 hours and 2 minutes after completion. After failure, schedule a retry for 15 minutes after completion.
   - Use a dated `StartCalendarInterval` so missed executions run after wake. Calendar scheduling may round up to the next minute; never send early.
   - A local-only scheduler may run at login and in response to a completed or interrupted attempt to update the next launchd execution. It must not invoke Codex.
   - If the Mac has been asleep, check immediately after waking. If more than 5 hours and 2 minutes have passed, send the heartbeat without waiting another five-hour interval.
   - Run automatically after login and survive system restarts.
   - Avoid concurrent heartbeat executions.

3. **Usage-limit handling**
   - If Codex has reached its five-hour or weekly usage limit, the heartbeat will fail.
   - Retry failed requests every 15 minutes until one succeeds.
   - Do not update the last-success timestamp after a failed request.
   - Distinguish successful model responses from usage-limit errors. Do not rely exclusively on the process exit code.
   - Handle network failures and other transient errors gracefully.

4. **Resource efficiency and safety**
   - Use `codex exec` in non-interactive mode.
   - Use ephemeral sessions to avoid accumulating unnecessary session history.
   - Prevent Codex from modifying files or executing unnecessary tools.
   - Keep token consumption as low as possible.
   - Avoid interfering with other Codex sessions or Git repositories.

5. **Implementation**
   - Create a Bash script that handles the heartbeat, timestamp tracking, error handling, logging, and retries.
   - Create a macOS LaunchAgent plist to schedule the script.
   - Detect the installed Codex executable rather than assuming its location.
   - Store persistent state in an appropriate user-level directory.
   - Provide commands for installation, checking status, inspecting logs, and uninstalling.
   - Make installation idempotent.

### Expected behavior

- If fewer than 5 hours and 2 minutes have elapsed since the last successful heartbeat, do nothing.
- Let the operating system launch the heartbeat at the next scheduled due time, without periodic checks in between.
- If the Mac was asleep when the interval expired, send the heartbeat as soon as possible after waking.
- If Codex rejects the request due to a usage limit, retry every 15 minutes until successful.
- Once a heartbeat succeeds, record its completion time and schedule the next heartbeat relative to that timestamp.
- Never consume Codex tokens merely to check whether a heartbeat is due.

### Important considerations

Verify the current Codex CLI syntax and macOS `launchd` behavior against official documentation before implementing.

The five-hour usage window begins with the first accepted Codex message after the previous window expires. The heartbeat's local timestamp is only an approximation of the actual usage-window start, especially when other Codex sessions are active.

Implement and test the solution, including failure handling, sleep/wake behavior, and duplicate-execution prevention. Do not claim that sleep/wake behavior has been tested unless it has actually been verified.

# Notes

- Make this app send push notifications when it runs showing what codex has said.
