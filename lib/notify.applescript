-- Arguments are data, never interpolated into executable AppleScript.
on run argv
    display notification (item 2 of argv) with title "Codex Heartbeat" subtitle (item 1 of argv)
end run
