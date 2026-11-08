#!/bin/bash
# Compatible with macOS's Bash 3.2. No Codex invocation on the not-due path.
set -euo pipefail
umask 077

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SUPPORT="$ROOT/lib/heartbeat.pl"
source "$ROOT/lib/common.sh"
log() {
    # Keep two bounded logs. Only the lock holder writes/rotates them.
    if [[ -f "$LOGS/heartbeat.log" ]] && (( $(/usr/bin/stat -f %z "$LOGS/heartbeat.log") > 1048576 )); then
        mv -f "$LOGS/heartbeat.log" "$LOGS/heartbeat.log.1"
    fi
    printf '%s %s\n' "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOGS/heartbeat.log"
}
case "${1:-check}" in
    status)
        success=$(read_stamp last-success)
        attempt=$(read_stamp last-attempt)
        current=$(now)
        due=$(due_time "$success" "$attempt")
        printf 'Last success (Unix): %s\nLast attempt (Unix): %s\nNext eligible heartbeat (Unix): %s\n' "$success" "$attempt" "$due"
        if [[ -f "$STATE/scheduled-for" ]]; then
            scheduled=$(read_stamp scheduled-for)
            printf 'Scheduled launch: %s (%s)\n' "$scheduled" "$(/bin/date -u -r "$scheduled" '+%Y-%m-%d %H:%M:%S UTC')"
        fi
        if (( current >= due )); then echo 'Heartbeat: due'; else echo "Heartbeat: due in $((due - current)) seconds"; fi
        printf 'State: %s\nLog: %s/heartbeat.log\n' "$STATE" "$LOGS"
        exit 0
        ;;
    check)
        mkdir -p "$STATE" "$LOGS"
        # Kernel lock survives parent death while descendants retain FD 9.
        # Never unlink this file: unlinking a locked inode permits two owners.
        exec 9>>"$STATE/heartbeat.lock"
        exec /usr/bin/perl "$SUPPORT" lock "$0"
        ;;
    --locked) ;;
    *) echo "Usage: $0 [check|status]" >&2; exit 64 ;;
esac

# Queue an event before doing work, so even a killed attempt is rescheduled.
# The scheduler waits on the same lock until all heartbeat processes exit.
if [[ -f "$STATE/enabled" ]]; then
    mkdir -p "$STATE/schedule-queue"
    touch "$STATE/schedule-queue/pending"
fi
success=$(read_stamp last-success)
attempt=$(read_stamp last-attempt)
current=$(now)
due=$(due_time "$success" "$attempt")
# A backward wall-clock adjustment must not wedge the scheduler for hours.
if (( success > current || attempt > current )); then
    if (( success > current )); then
        log 'clock moved backwards; waiting for recorded success time to catch up'
        # Keep the recorded success intact. Wait for wall time to catch up.
        exit 0
    fi
    log 'clock moved backwards; deferring one retry interval'
    write_stamp last-attempt "$current"
    exit 0
fi
(( current >= due )) || exit 0

# Write before execution so a crash, reboot, or kill still gets a retry delay.
write_stamp last-attempt "$current"
run_dir=$(mktemp -d "$STATE/run.XXXXXX")
cleanup() { rm -rf "$run_dir"; }
trap cleanup EXIT

codex_bin=${HEARTBEAT_CODEX_BIN:-}
if [[ -z "$codex_bin" && -r "$ROOT/codex-path" ]]; then codex_bin=$(cat "$ROOT/codex-path"); fi
if [[ ! -x "$codex_bin" ]]; then codex_bin=$(command -v codex || true); fi
if [[ -z "$codex_bin" || ! -x "$codex_bin" ]]; then
    write_stamp last-attempt "$(now)"
    log 'failure=executable-not-found; retry in 900 seconds (reinstall to refresh path)'
    exit 1
fi

# Put Codex in an empty directory, not a repository. Ignore user configuration
# so MCP servers, hooks, instructions and model/provider overrides cannot leak in.
mkdir "$run_dir/work"
args=(--no-daemon exec --ignore-user-config --ignore-rules --ephemeral --json
      --sandbox read-only --skip-git-repo-check --color never
      -C "$run_dir/work" -c 'approval_policy="never"'
      -c 'forced_login_method="chatgpt"' -c 'project_doc_max_bytes=0'
      -c 'model_reasoning_effort="low"' -c 'web_search="disabled"')
for feature in shell_tool shell_snapshot hooks apps plugins memories multi_agent browser_use computer_use image_generation; do
    args+=(--disable "$feature")
done
log 'heartbeat started'
rc=0
# No inherited stdin; supervisor bounds requests and kills only its own group.
/usr/bin/perl "$SUPPORT" run "${HEARTBEAT_TIMEOUT_SECONDS:-120}" \
    "$codex_bin" "${args[@]}" 'Reply with exactly OK. Do not use tools.' \
    > "$run_dir/events.jsonl" 2> "$run_dir/stderr" < /dev/null || rc=$?
result=$(/usr/bin/perl "$SUPPORT" validate "$run_dir/events.jsonl" "$run_dir/stderr" "$rc")
finished=$(now)
if [[ "$result" == success ]]; then
    write_stamp last-success "$finished"
    # Equal timestamps also represent a completed successful attempt.
    write_stamp last-attempt "$finished"
    log "success; next eligible at $((finished + INTERVAL))"
else
    write_stamp last-attempt "$finished"
    # Retain only the latest failure, capped to 64 KiB per stream, privately.
    /usr/bin/tail -c 65536 "$run_dir/events.jsonl" > "$STATE/last-failure.jsonl"
    /usr/bin/tail -c 65536 "$run_dir/stderr" > "$STATE/last-failure.stderr"
    log "failure=$result exit=$rc; retry at $((finished + RETRY))"
    exit 1
fi
