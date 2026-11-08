#!/bin/bash
# Event-driven: runs at login or when the heartbeat leaves a queue marker.
set -euo pipefail
umask 077
ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/lib/common.sh"
mkdir -p "$STATE/schedule-queue" "$LOGS" "$AGENTS"
if [[ "${1:-}" != --locked ]]; then
    exec 9>>"$STATE/heartbeat.lock"
    exec /usr/bin/perl "$ROOT/lib/heartbeat.pl" lock "$0" wait
fi
[[ -f "$STATE/enabled" ]] || exit 0
success=$(read_stamp last-success)
attempt=$(read_stamp last-attempt)
due=$(due_time "$success" "$attempt")
current=$(now)
export HEARTBEAT_IMMEDIATE=0
if (( due <= current )); then
    # Run immediately after bootstrap; the calendar is a fallback if needed.
    export HEARTBEAT_IMMEDIATE=1
    due=$((current + 60))
fi
# launchd's calendar has minute resolution. Round up, never send early.
export HEARTBEAT_WHEN=$(((due + 59) / 60 * 60))
pending=$(mktemp "$AGENTS/.$LABEL.XXXXXX")
trap 'rm -f "$pending"' EXIT
render_plist local.codex-heartbeat.plist.in > "$pending"
/usr/bin/plutil -lint "$pending" > /dev/null
if cmp -s "$pending" "$PLIST" && "$LAUNCHCTL" print "$SERVICE" > /dev/null 2>&1; then
    rm -f "$STATE/schedule-queue/pending" "$STATE/schedule-queue/processing"
    exit 0
fi
# This is a separate job, so it can replace the finished worker safely.
if "$LAUNCHCTL" print "$SERVICE" > /dev/null 2>&1; then "$LAUNCHCTL" bootout "$SERVICE"; fi
chmod 600 "$pending"
mv -f "$pending" "$PLIST"
write_stamp scheduled-for "$HEARTBEAT_WHEN"
# Keep an in-progress queue entry across bootstrap so a killed scheduler is
# restarted. A newly launched worker creates its own separate pending entry.
if [[ -f "$STATE/schedule-queue/pending" ]]; then
    mv -f "$STATE/schedule-queue/pending" "$STATE/schedule-queue/processing"
else
    touch "$STATE/schedule-queue/processing"
fi
# A due RunAtLoad worker must acquire the heartbeat lock after bootstrap.
exec 9>&-
[[ -f "$STATE/enabled" ]] || exit 0
if ! "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST"; then
    touch "$STATE/schedule-queue/pending"
    exit 1
fi
rm -f "$STATE/schedule-queue/processing"
