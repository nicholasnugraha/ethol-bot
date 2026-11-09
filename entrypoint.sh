#!/usr/bin/env bash
set -euo pipefail

if [ -z "${VNC_PASSWORD:-}" ]; then
  echo "ERROR: VNC_PASSWORD wajib diisi. Isi di .env supaya noVNC tidak terbuka tanpa password." >&2
  exit 1
fi

mkdir -p /data/.vnc /data/browser-profile
x11vnc -storepasswd "$VNC_PASSWORD" /data/.vnc/passwd >/dev/null 2>&1

# Virtual display for the headed Chromium that the user controls via noVNC.
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
for _ in $(seq 1 30); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
  echo "ERROR: Xvfb gagal dibuat di :99" >&2
  exit 1
fi

openbox >/dev/null 2>&1 &

x11vnc -display :99 -forever -shared -rfbport 5900 \
  -rfbauth /data/.vnc/passwd -quiet -noxdamage &
websockify --web=/usr/share/novnc 8080 localhost:5900 &

exec python -m app.main
