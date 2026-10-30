"""Versioned provider pricing, kept in data (not code), and Decimal cost calculation."""

import json
from datetime import datetime
from decimal import Decimal
from importlib import resources

from pydantic import BaseModel, ConfigDict

from muda.ai.types import TokenUsage


class Rates(BaseModel):
    model_config = ConfigDict(frozen=True)
    cache_hit: Decimal
    cache_miss: Decimal
    output: Decimal


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True)
    peak: Rates
    off_peak: Rates


class PricingTable(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    version: str
    source_url: str
    unit_tokens: int
    peak_weekdays_utc: tuple[int, ...]
    peak_hours_utc: tuple[tuple[int, int], ...]  # [start, end) in UTC hours
    models: dict[str, ModelPricing]


def load_pricing(name: str = "deepseek_pricing.json") -> PricingTable:
    raw = resources.files("muda.ai").joinpath("data", name).read_text(encoding="utf-8")
    return PricingTable.model_validate(json.loads(raw))


def _is_peak(table: PricingTable, at: datetime) -> bool:
    # Chinese public holidays are off-peak at the provider; treating them as peak
    # can only overestimate cost, which is the safe direction for budgeting.
    return at.weekday() in table.peak_weekdays_utc and any(
        start <= at.hour < end for start, end in table.peak_hours_utc
    )


def usage_cost(table: PricingTable, model: str, usage: TokenUsage, at: datetime) -> Decimal | None:
    """Estimated USD cost, or None when it cannot be determined (never assumed zero)."""
    pricing = table.models.get(model)
    if pricing is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    rates = pricing.peak if _is_peak(table, at) else pricing.off_peak
    hit = min(usage.cache_hit_tokens or 0, usage.input_tokens)  # no split reported -> all misses
    miss = usage.input_tokens - hit
    unit = Decimal(table.unit_tokens)
    return (
        Decimal(hit) * rates.cache_hit
        + Decimal(miss) * rates.cache_miss
        + Decimal(usage.output_tokens) * rates.output
    ) / unit


def worst_case_cost(
    table: PricingTable, model: str, *, input_tokens: int, max_output_tokens: int
) -> Decimal | None:
    """Upper bound used to reserve budget before a call: peak rates, no cache hits."""
    pricing = table.models.get(model)
    if pricing is None:
        return None
    unit = Decimal(table.unit_tokens)
    return (
        Decimal(input_tokens) * pricing.peak.cache_miss
        + Decimal(max_output_tokens) * pricing.peak.output
    ) / unit
