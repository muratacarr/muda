"""Runtime configuration from environment variables. Secrets are SecretStr and never logged."""

from decimal import Decimal
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    database_url: str = "sqlite:///data/muda.db"
    public_base_url: str = "http://localhost:8080"
    preview_secret: SecretStr
    preview_ttl_minutes: int = 30

    telegram_bot_token: SecretStr | None = None
    telegram_allowed_user_id: int | None = None

    ai_provider: Literal["deepseek"] = "deepseek"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    ai_timeout_s: float = 30.0
    ai_max_retries: int = 1
    ai_max_output_tokens: int = 400
    ai_max_input_chars: int = 6000
    ai_max_calls_per_job: int = 2
    ai_job_budget_usd: Decimal = Decimal("0.05")
    ai_monthly_budget_usd: Decimal = Decimal("1")

    max_prompt_chars: int = 1000
    rate_limit_messages_per_minute: int = 5
    rate_limit_ai_requests_per_minute: int = 2

    @field_validator("preview_secret")
    @classmethod
    def _strong_secret(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("PREVIEW_SECRET must be at least 32 characters")
        return v

    def secret_values(self) -> list[str]:
        """Every secret string, for log redaction."""
        secrets = [self.preview_secret, self.telegram_bot_token, self.deepseek_api_key]
        return [s.get_secret_value() for s in secrets if s is not None]
