# ETHOL Attendance Bot

Bot Telegram untuk memantau sesi presensi ETHOL PENS dan mengirim presensi atas nama satu akun mahasiswa. Login dilakukan manual lewat **noVNC** di dalam container, jadi NetID/password tidak pernah dikirim ke Telegram dan tidak disimpan sebagai password.

> **Penting:** endpoint mahasiswa ETHOL hanya mencatat **Hadir**. Tidak ada parameter Izin/Sakit/Alpha untuk mahasiswa. Karena itu tombol **Izin/Sakit** di bot hanya dicatat lokal di SQLite dan **tidak** mengirim apa pun ke ETHOL. Gunakan bot ini hanya saat kamu benar-benar menghadiri kelas.

## Fitur

- Login CAS interaktif lewat browser Chromium di VPS, dilihat dari noVNC.
- Sesi disimpan sebagai cookie terenkripsi (Fernet) di SQLite; token 15 menit di-refresh otomatis.
- Bot mengirim link noVNC ketika sesi habis (refresh token 7 hari).
- Menampilkan mata kuliah hari ini dari jadwal ETHOL.
- Memantau **semua mata kuliah yang diambil**, bukan hanya yang terjadwal hari ini. Sesi rapel/pengganti di hari atau jam lain juga terdeteksi.
- Tombol **Hadir / Izin / Sakit**.
- Mode **Konfirmasi** (default) atau **Auto**.
- Verifikasi hasil presensi lewat `riwayat`, dedup per `key`, dan log audit di SQLite.
- Allowlist Telegram chat ID.

## Arsitektur

```
Telegram (aiogram)
      │
      ▼
AppContext ──► Watcher (poll tiap 45 s) ──► EtholService
      │                                        │
      │                                        ├─ GET  /api/kuliah
      │                                        ├─ POST /api/kuliah/hari-kuliah-in
      │                                        ├─ GET  /api/presensi/aktif-kuliah
      │                                        ├─ GET  /api/presensi/riwayat
      │                                        └─ POST /api/presensi/mahasiswa
      ▼
SQLite (/data/app.db)  ◄── cookie terenkripsi, cache, log, status izin/sakit
      ▲
      │
AuthManager ──► Chromium (Xvfb :99) ──► noVNC :6080
```

## Struktur

```
ethol-bot/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh          # Xvfb + x11vnc + websockify + bot
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py            # bootstrap aiogram + background jobs
│   ├── config.py          # pydantic-settings
│   ├── context.py
│   ├── crypto.py          # Fernet
│   ├── db.py              # SQLite (aiosqlite)
│   ├── ethol/
│   │   ├── client.py      # httpx + cookie jar + refresh-on-401
│   │   ├── auth.py        # login CAS via Playwright, simpan cookie
│   │   ├── service.py     # cache jadwal, submit, verifikasi
│   │   └── models.py
│   ├── jobs/
│   │   ├── watcher.py     # polling sesi + session keeper
│   │   └── daily.py       # ringkasan harian
│   ├── bot/
│   │   ├── handlers.py
│   │   ├── keyboards.py
│   │   ├── notifications.py
│   │   └── formatters.py
│   └── utils/time.py
├── tests/
└── data/                  # volume: app.db, browser-profile, .vnc
```

## Setup

### 1. Buat bot Telegram

1. Chat `@BotFather`, `/newbot`, simpan token.
2. Chat `@userinfobot` untuk mendapatkan chat ID kamu.
3. Isi `TELEGRAM_BOT_TOKEN` dan `ALLOWED_CHAT_IDS` di `.env`.

### 2. Generate encryption key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Masukkan hasilnya ke `ENCRYPTION_KEY`. Jangan ganti setelah bot dipakai; cookie tersimpan tidak akan bisa didekripsi.

### 3. Siapkan `.env`

```bash
cp .env.example .env
nano .env
```

Minimal yang wajib:

```dotenv
TELEGRAM_BOT_TOKEN=...
ALLOWED_CHAT_IDS=123456789
ENCRYPTION_KEY=...
VNC_PASSWORD=password-panjang-acak
NOVNC_PUBLIC_URL=http://IP_VPS:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1
```

`NOVNC_PUBLIC_URL` harus bisa dibuka dari HP/laptop kamu. Kalau VPS punya domain dan TLS, gunakan `https://...`.

### 4. Jalankan

```bash
docker compose up -d --build
docker compose logs -f
```

Container menjalankan:

- Xvfb `:99` (layar virtual 1280×800)
- x11vnc `:5900` dengan password dari `VNC_PASSWORD`
- noVNC/websockify `:6080`
- bot Python

### 5. Login pertama

1. Di Telegram, kirim `/login`.
2. Tekan tombol **Buka noVNC**.
3. Masukkan password VNC (`VNC_PASSWORD`).
4. Di dalam browser VPS, login dengan NetID dan password PENS.
5. Setelah dashboard ETHOL terbuka, bot mendeteksi `validasi-token` 200 dan menyimpan cookie.
6. Bot mengirim konfirmasi **Login berhasil**.

Browser akan ditutup otomatis setelah login (kecuali `KEEP_BROWSER_OPEN=true`). Cookie dan `CASTGC` tersimpan di profil browser + SQLite, jadi login berikutnya biasanya mulus selama `refresh_token`/`CASTGC` masih hidup.

## Perintah

| Perintah | Fungsi |
|---|---|
| `/start` | Menu utama |
| `/hariini` | MK hari ini + status sesi |
| `/status` | Status sesi, mode, jumlah MK, log terakhir |
| `/on` | Aktifkan pemantauan |
| `/off` | Matikan pemantauan |
| `/mode` | Ganti Konfirmasi ↔ Auto |
| `/login` | Buka alur login noVNC |
| `/log` | 10 log presensi terakhir |
| `/izin <mk>` | Tandai izin lokal untuk hari ini |
| `/sakit <mk>` | Tandai sakit lokal untuk hari ini |
| `/batal <mk>` | Hapus status izin/sakit lokal |

`<mk>` bisa nomor kuliah (mis. `223349`) atau potongan nama (mis. `sistem`).

## Cara kerja pemantauan

1. Saat `/on`, watcher berjalan tiap `POLL_INTERVAL_SECONDS` (default 45 detik).
2. Watcher mengambil config tahun/semester dan **semua mata kuliah yang diambil**.
3. Untuk setiap MK, bot memanggil `GET /api/presensi/aktif-kuliah` dan mencari `open === 1` — tanpa memfilter hari/jam jadwal, sehingga sesi rapel juga terbaca.
4. Jika sesi terbuka dan `key` belum pernah tercatat hadir:
   - **Mode Konfirmasi:** bot mengirim pesan dengan tombol Hadir/Izin/Sakit.
   - **Mode Auto:** bot langsung `POST /api/presensi/mahasiswa`.
5. Setelah submit, bot memverifikasi `key` muncul di `riwayat`, lalu menulis log.
6. Notifikasi/percobaan auto dibatasi per `key` (cooldown 10 menit di mode Konfirmasi, 2 menit di mode Auto) agar tidak spam.

Jadwal harian tetap dipakai untuk tampilan `/hariini` dan label status di luar jam. `WINDOW_BEFORE_MINUTES`/`WINDOW_AFTER_MINUTES` hanya memengaruhi label itu, bukan cakupan pemantauan. Kalau ingin mengurangi jumlah request, naikkan `POLL_INTERVAL_SECONDS` ke 60–90.

`key` presensi berubah setiap dosen membuka sesi. Bot selalu mengambil `key` baru; tidak pernah di-cache.

## Endpoint ETHOL yang dipakai

| Kebutuhan | Endpoint |
|---|---|
| Tahun/semester aktif | `GET /api/auth/config` |
| Validasi sesi | `GET /api/auth/validasi-token` |
| Refresh token | `POST /api/auth/refresh` |
| Daftar MK | `GET /api/kuliah?tahun=&semester=` |
| Jadwal | `POST /api/kuliah/hari-kuliah-in` |
| Sesi aktif | `GET /api/presensi/aktif-kuliah?kuliah=&jenis_schema=` |
| Riwayat | `GET /api/presensi/riwayat?kuliah=&jenis_schema=&nomor=` |
| Kirim presensi | `POST /api/presensi/mahasiswa` |

Body presensi: `{kuliah, jenis_schema, mahasiswa, key, kuliah_asal}`. Respons: `{sukses, pesan}`.

## Keamanan

- **Jangan expose noVNC mentah ke internet tanpa TLS.** Password VNC dikirim lewat WebSocket; tanpa TLS isinya bisa disadap. Letakkan di belakang Caddy/Traefik dengan HTTPS, atau akses lewat SSH tunnel.
- Protokol VNC efektif membatasi password ~8 karakter, jadi `VNC_PASSWORD` bukan pengganti autentikasi kuat. Gunakan firewall/TLS/basic auth di depannya.
- Batasi port `6080` di firewall ke IP kamu jika memungkinkan.
- `ALLOWED_CHAT_IDS` wajib diisi; bot mengabaikan semua user lain.
- Password PENS tidak disimpan. Yang disimpan hanya cookie sesi terenkripsi dan profil browser.
- `ENCRYPTION_KEY`, `TELEGRAM_BOT_TOKEN`, dan `VNC_PASSWORD` hanya di `.env`; jangan commit.
- Log tidak mencetak cookie/token. Tetap jangan bagikan `docker compose logs` mentah-mentah.
- Jika VPS dicuri/di-snapshot, anggap sesi ETHOL bisa dipakai. Logout dari ETHOL atau hapus `data/` lalu login ulang.

Contoh Caddy di depan noVNC (opsional):

```caddyfile
ethol-bot.example.com {
    reverse_proxy 127.0.0.1:6080
    basicauth {
        kamu $2a$14$...   # caddy hash-password
    }
}
```

Dengan cara ini, `NOVNC_PUBLIC_URL=https://ethol-bot.example.com/vnc.html?autoconnect=1&resize=scale&reconnect=1` dan port `6080` tidak perlu dibuka ke publik.

## Troubleshooting

**noVNC blank / connection refused**
- `docker compose logs -f` — pastikan Xvfb, x11vnc, websockify hidup.
- Pastikan port `6080` terbuka di firewall VPS.
- Coba `http://IP:6080/vnc.html` tanpa query.

**Login berhasil di browser tapi bot bilang sesi habis**
- Tunggu beberapa detik; bot memeriksa `validasi-token` tiap 2 detik.
- Pastikan kamu benar-benar sampai di dashboard ETHOL, bukan halaman CAS yang error.
- Cek `docker compose logs` untuk pesan `Login CAS berhasil`.

**`database is locked`**
- Hanya jalankan satu container. Jangan menjalankan bot dua kali dengan volume yang sama.
- SQLite sudah memakai WAL + `busy_timeout`.

**`ENCRYPTION_KEY` berubah**
- Cookie lama tidak bisa didekripsi. Hapus `data/app.db` (atau setidaknya baris `ethol_session`) lalu `/login` ulang.

**Browser gagal dibuka / profile lock**
- `docker compose restart ethol-bot`.
- Jika masih gagal, hapus `data/browser-profile/Singleton*` lalu restart.

**Sesi habis tiap beberapa hari**
- Normal: `refresh_token` 7 hari. Bot mengirim link noVNC saat refresh gagal.
- Ringkasan harian juga mengingatkan kalau refresh token tersisa < 2 hari.

## Test

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  bash -lc "pip install -q -r requirements-dev.txt && pytest -q"
```

Atau di host dengan Python 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Test mencakup parsing model, window waktu, crypto, SQLite, cache jadwal, submit, dan penolakan `key` basi. Test tidak memanggil ETHOL dan tidak mengirim presensi.

## Batasan

- Endpoint ini adalah API internal SPA ETHOL, bukan API resmi. Bisa berubah kapan saja.
- Presensi hanya bisa dikirim saat dosen membuka sesi; bot tidak bisa membuka sesi.
- Status Izin/Sakit tidak tersedia untuk akun mahasiswa. Itu hanya catatan lokal.
- Rekap resmi ada di Online MIS dan menyusul ±1 hari.
- Gunakan hanya untuk akun sendiri dan hanya saat benar-benar hadir.
