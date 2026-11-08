# Best-effort desktop notifications. Delivery must not change heartbeat state.
notify() {
    local subtitle=$1 body=$2
    if ! /usr/bin/perl "$ROOT/lib/heartbeat.pl" run 10 \
        "${HEARTBEAT_OSASCRIPT:-/usr/bin/osascript}" "$ROOT/lib/notify.applescript" \
        "$subtitle" "$body" > /dev/null 2>&1 < /dev/null; then
        printf '%s notification submission failed\n' "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOGS/heartbeat.log"
    else
        printf '%s notification submitted\n' "$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$LOGS/heartbeat.log"
    fi
    return 0
}
