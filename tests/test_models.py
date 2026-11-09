from __future__ import annotations

from app.ethol.models import (
    ActiveSession,
    Course,
    HistoryItem,
    ScheduleItem,
    SessionProfile,
)


def test_course_from_api() -> None:
    course = Course.from_api(
        {
            "nomor": 223324,
            "kuliah_asal": 223325,
            "jenisSchema": 4,
            "matakuliah": {"nama": "Keamanan, Keselamatan, Kesehatan dan Lingkungan Hidup (K3L)"},
            "dosen": "Suhariningsih",
            "kode_kelas": "3 STr Telkom",
            "pararel": "A",
        }
    )
    assert course.nomor == 223324
    assert course.jenis_schema == 4
    assert course.kuliah_asal == 223325
    assert course.nama.startswith("Keamanan")
    assert course.to_dict()["dosen"] == "Suhariningsih"


def test_schedule_item_from_api() -> None:
    course = Course(
        nomor=223349,
        jenis_schema=4,
        nama="Sistem Komunikasi Optik",
        dosen="Karimatun Nisa",
    )
    item = ScheduleItem.from_api(
        {
            "kuliah": 223349,
            "hari": "Senin",
            "jam_awal": "10:30",
            "jam_akhir": "12:10",
            "nomor_hari": 1,
            "ruang": "SAW-05.07",
        },
        {223349: course},
    )
    assert item.nama == "Sistem Komunikasi Optik"
    assert item.dosen == "Karimatun Nisa"
    assert item.nomor_hari == 1


def test_active_session_from_api() -> None:
    session = ActiveSession.from_api(
        {"kuliah": 223349, "jenis_schema": 4, "key": "etuYGfxfu3", "open": 1},
        0,
        0,
    )
    assert session.key == "etuYGfxfu3"
    assert session.open is True


def test_active_session_open_strings() -> None:
    assert ActiveSession.from_api({"open": "1", "key": "x"}, 1, 4).open is True
    assert ActiveSession.from_api({"open": "true", "key": "x"}, 1, 4).open is True
    assert ActiveSession.from_api({"open": "0", "key": "x"}, 1, 4).open is False
    assert ActiveSession.from_api({"open": 0, "key": "x"}, 1, 4).open is False


def test_history_item_from_api() -> None:
    item = HistoryItem.from_api(
        {
            "nomor": 4924080,
            "tanggal": "26-10-2026 11:22:25",
            "waktu_indonesia": "Senin, 26 Oktober 2026 - 11:22:25",
            "key": "etuYGfxfu3",
        }
    )
    assert item.nomor == 4924080
    assert item.key == "etuYGfxfu3"


def test_session_profile_from_api() -> None:
    profile = SessionProfile.from_api(
        {
            "nomor": 27850,
            "nipnrp": "2225600043",
            "nama": "Nicholas Rassya Nugraha",
            "hakAkses": ["mahasiswa"],
        }
    )
    assert profile.nomor == 27850
    assert profile.hak_akses == ["mahasiswa"]
    assert profile.to_dict()["nama"] == "Nicholas Rassya Nugraha"
