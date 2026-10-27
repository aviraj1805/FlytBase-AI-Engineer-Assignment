"""Small helpers for the timestamps used across the index, CLI and agent tools."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional, Union

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
TimeLike = Union[str, datetime, None]


def to_ts(value: datetime) -> str:
    return value.strftime(TS_FORMAT)


def from_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT)


def resolve(value: TimeLike, ref_day: Optional[date] = None, *, end_of_minute: bool = False) -> Optional[str]:
    """Turn '00:01', '2026-10-27 12:00', or a datetime into the index's timestamp string.

    Bare clock times are interpreted on `ref_day` (the footage day), which is what an
    operator means by "show me what happened at 00:01".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_ts(value)
    text = value.strip().replace("T", " ")
    for fmt in (TS_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return to_ts(datetime.strptime(text, fmt))
        except ValueError:
            pass
    clock = time.fromisoformat(text if text.count(":") else f"{text}:00")
    if end_of_minute and clock.second == 0 and text.count(":") == 1:
        clock = clock.replace(second=59)
    return to_ts(datetime.combine(ref_day or date.today(), clock))
