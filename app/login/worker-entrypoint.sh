#!/bin/sh
set -eu
umask 077
mkdir -p /tmp/worker-profile /tmp/worker-home
export HOME=/tmp/worker-home
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
for _ in $(seq 1 60); do xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep .25; done
xdpyinfo -display :99 >/dev/null 2>&1 || { echo 'Xvfb failed' >&2; exit 1; }
openbox >/dev/null 2>&1 &
# TokenFile validates the unique per-worker token for HTTP and websocket requests.
# noVNC's web root is exposed only via this token-checked websockify process.
printf '%s: localhost:5900\n' "$PROXY_TOKEN" > /tmp/novnc-token
# BasicHTTPAuth --auth-source is a literal user:password value (not a file).
# --web-auth applies this one-time credential to HTTP as well as websockets.
websockify --web=/usr/share/novnc --web-auth --token-plugin=TokenFile --token-source=/tmp/novnc-token --auth-plugin=BasicHTTPAuth --auth-source="gateway:$PROXY_TOKEN" 6080 localhost:5900 &
x11vnc -display :99 -localhost -forever -shared -rfbport 5900 -nopw -quiet -noxdamage &
exec python /app/worker.py
