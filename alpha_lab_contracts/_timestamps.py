"""Strict timestamp parsing shared by the v1 policy and v2 request contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import re

_RFC3339 = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d+)?"
    r"(?P<offset>Z|(?P<sign>[+-])(?P<offset_hour>\d{2}):(?P<offset_minute>\d{2}))$"
)


def parse_rfc3339_utc(value: object, field: str = "timestamp") -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be RFC 3339 with an explicit UTC offset")
    match = _RFC3339.fullmatch(value)
    if not match:
        raise ValueError(f"{field} must be RFC 3339 with an explicit UTC offset")
    fraction = match.group("fraction")
    if fraction and len(fraction) > 7:
        raise ValueError(f"{field} precision exceeds supported microsecond precision")
    if match.group("offset") != "Z":
        hours = int(match.group("offset_hour"))
        minutes = int(match.group("offset_minute"))
        if hours > 23 or minutes > 59 or value.endswith("-00:00"):
            raise ValueError(f"{field} contains an invalid or unknown UTC offset")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid {field} timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)
