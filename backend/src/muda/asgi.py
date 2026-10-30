"""ASGI entrypoint: `uvicorn muda.asgi:app`."""

from muda.api import create_app
from muda.observability import configure_logging
from muda.persistence.db import create_db_engine
from muda.settings import Settings

_settings = Settings()
configure_logging(_settings.secret_values())
app = create_app(create_db_engine(_settings.database_url), _settings)
