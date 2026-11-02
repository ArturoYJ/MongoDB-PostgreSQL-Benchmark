"""Run-status classification for the canonical CSV status enumeration."""

from typing import Optional


def classify_status(
    oom_status: Optional[bool],
    worker_error: bool,
    spill: Optional[bool],
    telemetry_complete: bool,
) -> str:
    """Never label incomplete evidence as a successful measurement."""
    if oom_status is True:
        return "oom"
    if oom_status is None or worker_error or spill is None or not telemetry_complete:
        return "error"
    return "spill" if spill else "ok"
