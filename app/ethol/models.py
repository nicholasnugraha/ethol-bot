from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Course:
    nomor: int
    jenis_schema: int
    nama: str
    kuliah_asal: int | None = None
    dosen: str | None = None
    kode_kelas: str | None = None
    pararel: str | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> Course:
        mk = item.get("matakuliah") or {}
        kuliah_asal = item.get("kuliah_asal")
        return cls(
            nomor=int(item["nomor"]),
            jenis_schema=int(item["jenisSchema"]),
            nama=str(mk.get("nama") or item.get("nama") or "Tanpa nama"),
            kuliah_asal=int(kuliah_asal) if kuliah_asal is not None else None,
            dosen=item.get("dosen"),
            kode_kelas=item.get("kode_kelas"),
            pararel=item.get("pararel"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Course:
        return cls(**data)


@dataclass(slots=True)
class ScheduleItem:
    kuliah: int
    nama: str
    nomor_hari: int
    jam_awal: str
    jam_akhir: str
    ruang: str | None = None
    hari: str | None = None
    dosen: str | None = None

    @classmethod
    def from_api(
        cls,
        item: dict[str, Any],
        course_map: dict[int, Course] | None = None,
    ) -> ScheduleItem:
        kuliah = int(item["kuliah"])
        course = (course_map or {}).get(kuliah)
        return cls(
            kuliah=kuliah,
            nama=str(
                (course.nama if course else None)
                or item.get("matakuliah")
                or item.get("nama")
                or f"MK {kuliah}"
            ),
            nomor_hari=int(item["nomor_hari"]),
            jam_awal=str(item.get("jam_awal") or "00:00"),
            jam_akhir=str(item.get("jam_akhir") or "00:00"),
            ruang=item.get("ruang"),
            hari=item.get("hari"),
            dosen=(course.dosen if course else item.get("dosen")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleItem:
        return cls(**data)


@dataclass(slots=True)
class ActiveSession:
    kuliah: int
    jenis_schema: int
    key: str
    open: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(
        cls,
        item: dict[str, Any],
        fallback_kuliah: int,
        fallback_jenis_schema: int,
    ) -> ActiveSession:
        raw_open = item.get("open")
        is_open = raw_open is True or raw_open == 1 or str(raw_open).lower() in {"1", "true"}
        return cls(
            kuliah=int(item.get("kuliah") or fallback_kuliah),
            jenis_schema=int(item.get("jenis_schema") or fallback_jenis_schema),
            key=str(item.get("key") or ""),
            open=is_open,
            raw=item,
        )


@dataclass(slots=True)
class HistoryItem:
    nomor: int
    tanggal: str
    key: str
    waktu_indonesia: str | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> HistoryItem:
        return cls(
            nomor=int(item.get("nomor") or 0),
            tanggal=str(item.get("tanggal") or ""),
            key=str(item.get("key") or ""),
            waktu_indonesia=item.get("waktu_indonesia"),
        )


@dataclass(slots=True)
class SessionProfile:
    nomor: int
    nipnrp: str
    nama: str
    hak_akses: list[str]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SessionProfile:
        return cls(
            nomor=int(data["nomor"]),
            nipnrp=str(data.get("nipnrp") or ""),
            nama=str(data.get("nama") or ""),
            hak_akses=list(data.get("hakAkses") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionProfile:
        return cls(**data)


@dataclass(slots=True)
class SubmitResult:
    sukses: bool
    pesan: str
    verified: bool = False
