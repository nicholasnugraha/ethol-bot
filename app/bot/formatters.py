from __future__ import annotations

from datetime import datetime
from html import escape

from app.ethol.models import Course, ScheduleItem
from app.utils.time import format_date_id, format_time_range

STATUS_LABEL = {
    "belum": "⏳ belum ada sesi",
    "dibuka": "🟢 sesi dibuka",
    "hadir": "✅ sudah presensi",
    "izin": "📝 izin (lokal)",
    "sakit": "🤒 sakit (lokal)",
    "skip": "⏭️ dilewati",
    "lewat": "⌛ di luar jam",
}


def format_today_schedule(
    items: list[ScheduleItem],
    now: datetime,
    status_map: dict[int, str] | None = None,
) -> str:
    status_map = status_map or {}
    lines = [f"📅 <b>{format_date_id(now)}</b>", ""]

    if not items:
        lines.append("Tidak ada mata kuliah hari ini.")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        status = status_map.get(item.kuliah, "belum")
        lines.append(f"<b>{index}. {escape(item.nama)}</b>")
        lines.append(
            f"🕐 {format_time_range(item.jam_awal, item.jam_akhir)}"
            f" · 📍 {escape(item.ruang or '-')}"
            f" · 🆔 <code>{item.kuliah}</code>"
        )
        if item.dosen:
            lines.append(f"👨‍🏫 {escape(item.dosen)}")
        lines.append(f"Status: {STATUS_LABEL.get(status, status)}")
        lines.append("")

    return "\n".join(lines).strip()


def format_course(course: Course) -> str:
    parts = [f"<b>{escape(course.nama)}</b>"]
    if course.dosen:
        parts.append(f"👨‍🏫 {escape(course.dosen)}")
    if course.kode_kelas:
        parts.append(f"🏷️ {escape(course.kode_kelas)}")
    return "\n".join(parts)


def format_log_entry(entry: dict) -> str:
    status = "✅" if entry.get("status") == "hadir" else "⚠️"
    pesan = escape(entry.get("pesan") or "-")
    return (
        f"{status} <code>{escape(entry.get('tanggal', '-'))}</code>"
        f" · MK <code>{entry.get('kuliah')}</code>\n"
        f"<i>{pesan}</i>"
    )
