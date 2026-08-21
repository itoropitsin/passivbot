# Aborted exploratory run

This run was intentionally stopped before completion on 18 August 2026.

Reason: with six nominal 14,400-second segments and a 60-second polling
interval, the loop can finish the last segment before collecting a sample at
the segment boundary. That makes the measured source coverage slightly shorter
than the required 24 hours. The run is retained as partial public-only
evidence and must not be submitted as a readiness gate.

Observed before stop: 16 public market snapshots, `fixture_mode=false`, no
private calls, no exchange writes, and no result package. A new run uses a
separate directory and 14,520-second segments.
