# ETHOL Attendance Bot — multi-user

Telegram bot with per-user ETHOL sessions and temporary Playwright/noVNC login containers, reachable through one HTTPS domain.

Use attendance actions only when actually attending class and in accordance with institutional rules. Izin/Sakit is a local note, not an ETHOL submission.

## Architecture

- Allowlisted Telegram **user IDs**, private chats only. Each user owns their settings, encrypted session, caches, logs, notification deduplication, and jobs.
- The bot runs an HTTP/WebSocket login gateway on port 8080 behind your TLS reverse proxy.
- `/login` allocates one temporary headed Chromium/Xvfb/x11vnc/websockify container. Profiles and displays are never shared between login attempts.
- A short-lived, one-use bearer link exchanges its bootstrap token for a path-scoped HttpOnly/Secure/SameSite cookie. Static resources require that cookie; WebSocket connections also require the configured public Origin.
- The gateway routes traffic to private worker addresses. No worker ports are published. Worker-to-gateway results are authenticated using a per-attempt signature.
- Captured cookies are encrypted with Fernet. Normal ETHOL access uses per-user HTTP clients, not persistent desktops. Login workers are removed after completion, failure, expiry, and shutdown. Startup removes orphan workers with the same deployment label.

## Deployment prerequisites

Linux Docker Engine, Docker Compose, an HTTPS domain/reverse proxy with WebSocket support, and access to Telegram and CAS/ETHOL. Only one bot instance may manage a given deployment ID and Telegram polling token.

The bot mounts the Docker socket to launch workers. **This grants host-level control to the bot process.** Deploy on a dedicated trusted host or use an appropriately restricted external container broker. Worker containers do not receive the socket, host data mounts, or the bot encryption key.

Workers are resource-limited, read-only containers with temporary writable directories and dropped capabilities. Chromium currently runs as root with `--no-sandbox`; container isolation is not a substitute for the Chromium sandbox. The shared worker bridge is not a hardened network boundary against a compromised browser. Harden the worker user/browser sandbox and apply egress/network policies before exposing this as an untrusted public service. This implementation is intended for a small trusted allowlist.

## Configuration

Copy `.env.example` to `.env` and set:

```dotenv
TELEGRAM_BOT_TOKEN=your-token
ALLOWED_CHAT_IDS=101,202
ENCRYPTION_KEY=your-fernet-key
PUBLIC_BASE_URL=https://bot.example.com
LOGIN_WORKER_IMAGE=ethol-login-worker:latest
LOGIN_WORKER_NETWORK=ethol-login
LOGIN_DEPLOYMENT_ID=ethol-bot
LOGIN_MAX_WORKERS=3
```

Despite the legacy name, `ALLOWED_CHAT_IDS` must contain positive Telegram **user** IDs. Group operation is rejected. Generate a key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the key stable and back it up securely; changing it makes stored cookies unreadable. Do not reuse old persistent Chromium profile directories for multiple users. The old shared VNC password/public URL settings are no longer used by the login flow.

### Existing single-user database

Stop the old deployment and back up the entire data directory (including any SQLite WAL/SHM files) before upgrading. Set `LEGACY_OWNER_ID` to the Telegram user ID that owns the existing ETHOL account:

```dotenv
LEGACY_OWNER_ID=101
```

A nonempty legacy database fails closed without this explicit assignment. Migration moves all legacy account data to that owner transactionally. The owner must also be allowlisted. Empty legacy schemas can migrate without assignment. Keep the backup until validation is complete; schema downgrade is not supported.

### Build and run

Build the worker image **before** requesting login:

```bash
docker build -f app/login/worker.Dockerfile -t ethol-login-worker:latest .
docker compose up -d --build
docker compose logs -f ethol-bot
```

The default Compose bridge is `ethol-login`; the bot is reachable inside it as `ethol-bot`. Changing the worker network or bot service alias requires corresponding deployment configuration. The gateway is bound to host loopback port 8080, not exposed publicly without TLS.

Example Nginx location inside an existing HTTPS server for `bot.example.com`:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 900s;
    # Bootstrap URLs contain credentials: do not log them.
    access_log off;
}
```

Terminate TLS at the proxy and ensure `PUBLIC_BASE_URL` matches the exact browser origin. Do not expose worker ports 5900/6080. Exclude login URLs from proxy, CDN, analytics, and error-reporting logs. Ideally deny public access to `/internal/` while retaining container-to-bot access for signed worker callbacks.

## User flow

1. Send `/start` to the bot in a private chat.
2. Send `/login`; open the private one-use link without forwarding it. Anyone possessing an unused link can redeem it; Telegram user identity is established when the link is issued, not reauthenticated in the browser.
3. Enter CAS credentials in the remote Chromium window. The bot receives session cookies, not a stored password.
4. After login validation, the session is saved for that Telegram user and the temporary desktop is destroyed.
5. Use `/hariini`, `/status`, `/on`, `/off`, `/mode`, and attendance controls. Notifications go only to the account owner.

The default login timeout is five minutes. Capacity is bounded by `LOGIN_MAX_WORKERS`; one active attempt per user is permitted. Wait for expiration if an attempt is abandoned. A used link cannot be redeemed again; retain the opened browser session while it is active.

Account switching is deliberately rejected if the newly validated ETHOL identity differs from the stored one. An administrator-managed unlink/relink workflow is not implemented; do not manually reassign another user's database records.

Attendance submission claims are durable and user-scoped. An uncertain or failed submission is not automatically retried for the same course/session claim; check ETHOL history and reconcile deliberately. This favors avoiding duplicate submissions over automatic retry.

## Tests and validation

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Tests cover tenant storage and migration, session generation guards, user-scoped runtime, HTTP client behavior, durable submission claims, one-use login links, callback signatures, and fake-upstream HTTP/WebSocket proxy behavior.

A real deployment still needs an end-to-end acceptance test: build both images, log in as two different users concurrently, verify separate displays and sessions, verify wrong/expired cookie and Origin rejection, and confirm worker removal after success/timeout/restart. Docker Engine was unavailable in the development environment, so real container startup and CAS login were **not** verified here.
