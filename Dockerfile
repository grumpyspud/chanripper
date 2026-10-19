FROM python:3.12-slim

LABEL org.opencontainers.image.title="ChanRipper" \
      org.opencontainers.image.description="4chan thread downloader and monitor with a web UI" \
      org.opencontainers.image.source="https://github.com/grumpyspud/chanripper"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHANRIPPER_CONFIG_DIR=/config \
    CHANRIPPER_DOWNLOAD_DIR=/downloads \
    CHANRIPPER_PORT=4848 \
    PUID=1000 \
    PGID=1000 \
    TZ=UTC

# gosu lets the entrypoint drop from root to PUID/PGID; curl backs the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu curl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 appuser \
 && useradd -u 1000 -g 1000 -M -s /usr/sbin/nologin appuser

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ./core/
COPY web/ ./web/
COPY cli.py webmain.py ./

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/config", "/downloads"]
EXPOSE 4848

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${CHANRIPPER_PORT}/healthz" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "webmain.py"]
