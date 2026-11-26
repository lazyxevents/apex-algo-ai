"""Server-side execution mode guard. This release intentionally supports no live mode."""
from enum import StrEnum


class ExecutionMode(StrEnum):
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


def validate_execution_mode(mode: ExecutionMode | str) -> ExecutionMode:
    normalized = ExecutionMode(str(mode).upper())
    if normalized == ExecutionMode.LIVE:
        raise PermissionError("LIVE execution is not implemented or enabled in APEX Forex Engine")
    return normalized
