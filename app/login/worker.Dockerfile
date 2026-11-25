FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DISPLAY=:99 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates novnc websockify openbox x11vnc xvfb x11-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir 'playwright>=1.49,<2' 'httpx>=0.27,<1' && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY app/login/worker.py /app/worker.py
COPY app/login/worker-entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
EXPOSE 6080
ENTRYPOINT ["/entrypoint.sh"]
