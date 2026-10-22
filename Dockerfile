# syntax=docker/dockerfile:1.7
# permitA38 – multi-stage image. Build:  docker build -t permita38 .
# Dependencies are pinned by docker/constraints.txt (refresh with `make docker-lock`).

# Base image; override to pin a digest or use a registry mirror:
#   docker build --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> .
ARG PYTHON_IMAGE=python:3.12-slim

# ---------- build stage: install the package into a virtualenv ----------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY docker/constraints.txt ./docker/constraints.txt
COPY permita38 ./permita38
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -c docker/constraints.txt .

# ---------- runtime stage ----------
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="permitA38" \
      org.opencontainers.image.description="Time tracking, planning, offers, invoices and tax reserves for freelancers" \
      org.opencontainers.image.source="https://github.com/roggenbrot42/permitA38" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PERMITA38_IN_CONTAINER=1 \
    PERMITA38_DATA=/data \
    PERMITA38_WEB_DIR=/app/web \
    PERMITA38_HOST=0.0.0.0 \
    PERMITA38_PORT=8765

# unprivileged user; /data is owned by it, so a fresh named volume is writable out of the box
RUN groupadd --system --gid 10001 permita \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin permita \
    && mkdir /data /app \
    && chown permita:permita /data

COPY --from=builder /opt/venv /opt/venv
COPY web /app/web

USER 10001:10001
WORKDIR /data
VOLUME /data
EXPOSE 8765

# python instead of curl: the slim image has no curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PERMITA38_PORT', '8765'), timeout=3)"]

ENTRYPOINT ["permita38"]
CMD ["--host", "0.0.0.0", "--no-browser", "serve"]
