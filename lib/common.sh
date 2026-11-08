# Shared Bash 3.2 state and scheduling utilities. Sourced by bin scripts.
BASE=${HEARTBEAT_BASE_DIR:-"$HOME/Library/Application Support/CodexHeartbeat"}
STATE=${HEARTBEAT_STATE_DIR:-"$BASE/state"}
LOGS=${HEARTBEAT_LOG_DIR:-"$HOME/Library/Logs/CodexHeartbeat"}
AGENTS=${HEARTBEAT_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
LABEL=${HEARTBEAT_LABEL:-local.codex-heartbeat}
[[ "$LABEL" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Invalid service label' >&2; exit 64; }
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
LAUNCHCTL=${HEARTBEAT_LAUNCHCTL:-/bin/launchctl}
PLIST="$AGENTS/$LABEL.plist"
SCHEDULER_PLIST="$AGENTS/$LABEL.scheduler.plist"
INTERVAL=18120
RETRY=900
export HEARTBEAT_BASE_DIR="$BASE" HEARTBEAT_STATE_DIR="$STATE" HEARTBEAT_LOG_DIR="$LOGS"
export HEARTBEAT_AGENTS_DIR="$AGENTS" HEARTBEAT_LABEL="$LABEL"

now() { /bin/date +%s; }
read_stamp() {
    local value=0
    if [[ -f "$STATE/$1" ]]; then
        value=$(cat "$STATE/$1")
        if [[ ! "$value" =~ ^[1-9][0-9]{0,10}$ ]]; then
            echo "Invalid timestamp: $STATE/$1" >&2
            return 1
        fi
    fi
    printf '%s\n' "$value"
}
write_stamp() {
    printf '%s\n' "$2" > "$STATE/$1.new"
    mv -f "$STATE/$1.new" "$STATE/$1"
}
due_time() {
    local success=$1 attempt=$2 due=0
    (( success == 0 )) || due=$((success + INTERVAL))
    if (( attempt > success && attempt + RETRY > due )); then due=$((attempt + RETRY)); fi
    printf '%s\n' "$due"
}
render_plist() {
    export HEARTBEAT_INSTALL="$BASE" HEARTBEAT_STATE="$STATE" HEARTBEAT_LOGS="$LOGS"
    export HEARTBEAT_AGENTS="$AGENTS" HEARTBEAT_EXEC_PATH="$PATH"
    export HEARTBEAT_AUTH_HOME=${CODEX_HOME:-"$HOME/.codex"}
    /usr/bin/perl "$ROOT/lib/heartbeat.pl" plist "$ROOT/launchd/$1"
}
