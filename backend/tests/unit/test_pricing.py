from datetime import UTC, datetime
from decimal import Decimal

from muda.ai.pricing import load_pricing, usage_cost, worst_case_cost
from muda.ai.types import TokenUsage

PRICING = load_pricing()
MODEL = "deepseek-flash"
PEAK = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)  # Wednesday 07:00 UTC
OFF_PEAK_HOUR = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)  # Wednesday 12:00 UTC
WEEKEND = datetime(2026, 10, 31, 7, 0, tzinfo=UTC)  # Saturday 07:00 UTC


def test_pricing_is_versioned_and_has_source() -> None:
    assert PRICING.version
    assert PRICING.source_url.startswith("https://")
    assert MODEL in PRICING.models


def test_cost_uses_decimal_and_peak_rates() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_hit_tokens=0)
    cost = usage_cost(PRICING, MODEL, usage, PEAK)
    assert isinstance(cost, Decimal)
    # peak: miss 0.3 + output 1.2
    assert cost == Decimal("1.5")


def test_off_peak_is_cheaper_in_weekday_gap_and_on_weekend() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_hit_tokens=0)
    assert usage_cost(PRICING, MODEL, usage, OFF_PEAK_HOUR) == Decimal("0.75")
    assert usage_cost(PRICING, MODEL, usage, WEEKEND) == Decimal("0.75")


def test_cache_hit_tokens_are_priced_separately() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0, cache_hit_tokens=400_000)
    # peak: 0.4M * 0.006 + 0.6M * 0.3
    assert usage_cost(PRICING, MODEL, usage, PEAK) == Decimal("0.1824")


def test_missing_cache_split_is_priced_as_all_misses() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=0)
    assert usage_cost(PRICING, MODEL, usage, PEAK) == Decimal("0.3")


def test_unknown_usage_gives_unknown_cost_not_zero() -> None:
    assert usage_cost(PRICING, MODEL, TokenUsage(), PEAK) is None
    assert usage_cost(PRICING, MODEL, TokenUsage(input_tokens=10), PEAK) is None


def test_unknown_model_gives_unknown_cost() -> None:
    usage = TokenUsage(input_tokens=10, output_tokens=10)
    assert usage_cost(PRICING, "mystery-model", usage, PEAK) is None


def test_worst_case_assumes_peak_and_all_misses_regardless_of_time() -> None:
    wc = worst_case_cost(PRICING, MODEL, input_tokens=1_000_000, max_output_tokens=1_000_000)
    assert wc == Decimal("1.5")
