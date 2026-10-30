# syntax=docker/dockerfile:1
# Multi-arch friendly (runs natively on linux/arm64 for the Raspberry Pi 5).
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir /data && chown app:app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY docker-entrypoint.sh ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////data/muda.db
USER app
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
CMD ["api"]
