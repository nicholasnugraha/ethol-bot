from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.bot.formatters import format_log_entry, format_today_schedule
from app.ethol.models import ScheduleItem

JAKARTA = ZoneInfo("Asia/Jakarta")
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=JAKARTA)


def test_format_today_schedule_escapes_html() -> None:
    items = [
        ScheduleItem(
            kuliah=223349,
            nama="Sistem <Komunikasi> & Optik",
            nomor_hari=1,
            jam_awal="10:30",
            jam_akhir="12:10",
            ruang="SAW-05.07",
            dosen="Dosen & Rekan",
        )
    ]
    text = format_today_schedule(items, NOW, {223349: "dibuka"})
    assert "&lt;Komunikasi&gt;" in text
    assert "&amp;" in text
    assert "223349" in text
    assert "sesi dibuka" in text


def test_format_today_schedule_empty() -> None:
    text = format_today_schedule([], NOW)
    assert "Tidak ada mata kuliah" in text


def test_format_log_entry() -> None:
    text = format_log_entry(
        {
            "tanggal": "2026-11-02",
            "kuliah": 223349,
            "status": "hadir",
            "pesan": "Presensi <berhasil> & dicatat",
        }
    )
    assert "✅" in text
    assert "&lt;berhasil&gt;" in text
    assert "&amp;" in text
