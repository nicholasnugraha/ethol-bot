from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.utils.time import format_date_id, in_window, parse_hhmm, today_str

JAKARTA = ZoneInfo("Asia/Jakarta")


def test_parse_hhmm_accepts_seconds() -> None:
    assert parse_hhmm("10:30") == (10, 30)
    assert parse_hhmm("10:30:00") == (10, 30)


def test_parse_hhmm_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        parse_hhmm("10")


def test_in_window_normal() -> None:
    now = datetime(2026, 11, 2, 10, 30, tzinfo=JAKARTA)
    assert in_window(now, "10:30", "12:10", 10, 10) is True

    before = datetime(2026, 11, 2, 10, 19, tzinfo=JAKARTA)
    assert in_window(before, "10:30", "12:10", 10, 10) is False

    after = datetime(2026, 11, 2, 12, 20, tzinfo=JAKARTA)
    assert in_window(after, "10:30", "12:10", 10, 10) is True

    too_late = datetime(2026, 11, 2, 12, 21, tzinfo=JAKARTA)
    assert in_window(too_late, "10:30", "12:10", 10, 10) is False


def test_in_window_zero_margin() -> None:
    start = datetime(2026, 11, 2, 10, 30, tzinfo=JAKARTA)
    end = datetime(2026, 11, 2, 12, 10, tzinfo=JAKARTA)
    assert in_window(start, "10:30", "12:10", 0, 0) is True
    assert in_window(end, "10:30", "12:10", 0, 0) is True
    assert in_window(
        datetime(2026, 11, 2, 10, 29, tzinfo=JAKARTA),
        "10:30",
        "12:10",
        0,
        0,
    ) is False


def test_format_date_id() -> None:
    value = datetime(2026, 11, 2, 10, 0, tzinfo=JAKARTA)
    assert format_date_id(value) == "Senin, 2 November 2026"


def test_today_str() -> None:
    value = datetime(2026, 11, 2, 23, 59, tzinfo=JAKARTA)
    assert today_str(value) == "2026-11-02"
