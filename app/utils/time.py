from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

HARI_ID = {
    1: "Senin",
    2: "Selasa",
    3: "Rabu",
    4: "Kamis",
    5: "Jumat",
    6: "Sabtu",
    7: "Minggu",
}

BULAN_ID = {
    1: "Januari",
    2: "Februari",
    3: "Maret",
    4: "April",
    5: "Mei",
    6: "Juni",
    7: "Juli",
    8: "Agustus",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Desember",
}


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now_jakarta(tz: str = "Asia/Jakarta") -> datetime:
    return datetime.now(_zone(tz))


def today_str(now: datetime | None = None, tz: str = "Asia/Jakarta") -> str:
    current = now or now_jakarta(tz)
    return current.astimezone(_zone(tz)).date().isoformat()


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"Format jam tidak valid: {value!r}")
    return int(parts[0]), int(parts[1])


def in_window(
    now: datetime,
    start: str,
    end: str,
    before_minutes: int = 10,
    after_minutes: int = 10,
) -> bool:
    """True when `now` is inside [start - before, end + after]."""
    start_h, start_m = parse_hhmm(start)
    end_h, end_m = parse_hhmm(end)

    start_dt = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end_dt = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    return (
        start_dt - timedelta(minutes=before_minutes)
        <= now
        <= end_dt + timedelta(minutes=after_minutes)
    )


def format_date_id(value: datetime) -> str:
    return f"{HARI_ID[value.isoweekday()]}, {value.day} {BULAN_ID[value.month]} {value.year}"


def format_time_range(start: str, end: str) -> str:
    if not start or not end or start == "-" or end == "-":
        return "-"
    return f"{start}\u2013{end}"


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
