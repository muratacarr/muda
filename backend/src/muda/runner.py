"""Bot + worker process (separate from the public API so the site survives its failures)."""

import asyncio
import logging
import signal
from datetime import timedelta
from decimal import Decimal

from pydantic import SecretStr
from sqlalchemy.orm import Session

from muda.ai.budget import BudgetLimits
from muda.ai.deepseek import DeepSeekConfig, DeepSeekProvider
from muda.ai.pricing import load_pricing
from muda.bot_service import BotService
from muda.observability import configure_logging
from muda.persistence import jobs, publishing
from muda.persistence.db import create_db_engine
from muda.settings import Settings
from muda.telegram_adapter import PtbSender, TelegramNotifier, build_application
from muda.worker import CancelSignals, JobProcessor

logger = logging.getLogger("muda.runner")


async def run() -> None:
    settings = Settings()
    configure_logging(settings.secret_values())
    if settings.deepseek_api_key is None:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    engine = create_db_engine(settings.database_url)
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)

    provider = DeepSeekProvider(
        DeepSeekConfig(
            api_key=SecretStr(settings.deepseek_api_key.get_secret_value()),
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            timeout_s=settings.ai_timeout_s,
            max_retries=settings.ai_max_retries,
            max_output_tokens=settings.ai_max_output_tokens,
            max_input_chars=settings.ai_max_input_chars,
        )
    )
    signals = CancelSignals()
    application = build_application(settings, BotService(engine, settings, signals))
    processor = JobProcessor(
        engine,
        provider,
        pricing=load_pricing(),
        limits=BudgetLimits(
            max_calls_per_job=settings.ai_max_calls_per_job,
            per_job_usd=Decimal(settings.ai_job_budget_usd),
            monthly_usd=Decimal(settings.ai_monthly_budget_usd),
        ),
        model=settings.deepseek_model,
        max_output_tokens=settings.ai_max_output_tokens,
        notifier=TelegramNotifier(engine, settings, PtbSender(application.bot)),
        signals=signals,
        approval_ttl=timedelta(minutes=settings.preview_ttl_minutes),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    async with application:
        await application.start()
        if application.updater is None:
            raise RuntimeError("updater unavailable")
        # Long polling; pending updates are kept so an approval pressed during downtime is not lost.
        await application.updater.start_polling(drop_pending_updates=False)
        worker = asyncio.create_task(processor.run_forever(stop))
        logger.info("muda_started")
        await stop.wait()
        await application.updater.stop()
        await application.stop()
        await worker


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
