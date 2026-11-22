FROM python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN python -m pip install --upgrade "pip==26.3"

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS runner

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Refresh this value when reviewing OS security updates, including on Railway.
ARG SYSTEM_PACKAGES_REFRESH=2026-11-10
RUN test -n "$SYSTEM_PACKAGES_REFRESH" \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

COPY --from=builder /wheels /wheels
RUN python -m pip install --upgrade "pip==26.3" \
    && python -m pip install --no-index --find-links=/wheels family-ai-life-os \
    && rm -rf /wheels

COPY --chown=appuser:appuser alembic.ini main.py ./
COPY --chown=appuser:appuser alembic ./alembic

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os; os.kill(1, 0)"

STOPSIGNAL SIGTERM
CMD ["python", "main.py"]
