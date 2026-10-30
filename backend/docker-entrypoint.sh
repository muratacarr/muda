#!/bin/sh
set -eu

case "${1:-api}" in
  api)
    # The API container owns schema migrations; the bot waits for it to be healthy.
    alembic upgrade head
    exec uvicorn muda.asgi:app --host 0.0.0.0 --port 8000 --no-server-header
    ;;
  bot)
    exec python -m muda.runner
    ;;
  backup)
    exec python -m muda.backup
    ;;
  *)
    echo "unknown command: $1" >&2
    exit 2
    ;;
esac
