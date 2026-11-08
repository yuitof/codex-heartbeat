# Verification record

Verified on 2026-11-08, macOS Darwin 25.6.0 (arm64), Codex CLI 0.167.0.
The implementation was checked against the official documentation linked in
[README.md](README.md) and the installed CLI help and launchd manual.

| Check | Observed result |
| --- | --- |
| Bash syntax and Perl compilation | Passed |
| Offline integration suite | All 19 tests passed after the event-driven scheduling change |
| Failure classification | Simulated zero-exit usage limits, network errors, partial/malformed/wrong responses, unexpected tools and nonzero-exit `OK` were rejected |
| Retry state | Failed attempts preserved success and deferred the next request for 900 seconds; later success restarted the 18,120-second interval |
| Concurrent invocations | Eight simultaneous checks produced one fake model call |
| Killed parent | A surviving child retained the kernel lock and prevented another call |
| Timeout | Timed-out fake request and its descendant were terminated; lock subsequently released |
| Installer lifecycle | Isolated install twice/uninstall twice preserved state, rendered valid XML with spaces and ampersands, and avoided duplicate bootstrap of both agents |
| Scheduler deadlines | Success armed completion + 18,120 seconds; failure armed completion + 900 seconds; both rounded up to minute resolution |
| Scheduling recovery | A failed bootstrap retained the queued request; a late scheduler did not resurrect an uninstalled agent |
| Scheduler concurrency | Scheduler waited for an active heartbeat to finish before changing its job |
| Calendar boundary | A deadline crossing December 31 generated the correct January 1 calendar fields |
| Actual launchd integration | Temporary scheduler armed a specific calendar date, the fake heartbeat fired at that date, failure armed a 15-minute retry, an overdue retry ran immediately, and success armed the next 302-minute deadline |
| Absence of polling | Over 65 idle seconds, the real launchd worker run count and fake model call count remained unchanged |
| Actual Codex response | One read-only ephemeral real request passed strict validation at 2026-11-08 06:32:50 UTC |
| Production installation | Event-driven replacement installed and both agents loaded; heartbeat has specific Month/Day/Hour/Minute fields and the scheduler has no periodic timer; repeat installation preserved state |
| Elapsed time after a simulated sleep gap | A three-day-old success triggered an immediate fake heartbeat |
| Physical Mac sleep/wake | **Not tested**; the README contains the manual acceptance procedure |
| Physical restart/login | **Not tested**; persistence is configured through the user LaunchAgent |
| Actual five-hour/weekly quota exhaustion | **Not induced**; failure handling was tested with fake CLI error streams |

The production next-eligible timestamp following the real heartbeat is
`1794137690` (2026-11-08 11:34:50 UTC). The dated launch is rounded up to
11:35 UTC if the Mac is awake and the user is logged in. This is a local estimate,
not a measurement of Codex's actual usage window.

The first launchd test inside the execution sandbox could not bootstrap the
temporary job. It passed when run with permission to register a user agent.
No physical sleep or restart was performed, and no test requested usage status.
The scheduler update tests used only fake Codex responses; no additional real
model request was needed to change the scheduling behavior.
The installed success timestamp remained `1794119570`, and the actual dated
calendar launch is `1794137700` (2026-11-08 11:35:00 UTC). The replacement worker
had zero executions at installation, confirming migration did not send another
heartbeat.
