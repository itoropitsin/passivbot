# Aborted public shadow run

This run is retained as partial public-only evidence and must not be submitted
to the shadow readiness gate.

The supervisor stopped updating at 2026-08-18T15:14:50Z after 158 market
snapshots and 1,282 runtime events. Neither `result.json` nor
`process_supervisor.json` was produced, so the 24-hour duration and restart
evidence are not proven. No exchange write operation was observed in the
persisted events before the interruption.

The run is not resumable as a readiness run. A new run must use a new output
directory and the detached-launch procedure recorded in the implementation
review.
