# ADR 0005: Provider adapter, Decimal costs, budget reservation

Status: accepted

**Decision.** `AIProvider` is a Protocol; `DeepSeekProvider` hides HTTP, model settings, JSON-mode
parsing and error normalization. Prices live in a versioned data file (`ai/data/deepseek_pricing.json`)
with peak/off-peak rates, never in code. Costs are `Decimal`, stored as text because SQLite `NUMERIC`
round-trips through float. Unknown usage means unknown cost (NULL), never zero.

**Budget.** Before each call the worst-case cost (peak rate, no cache hits, max output tokens) is reserved
against a per-job and a monthly limit; unknown-cost records are charged pessimistically. Retries happen
only when no response was received, because a response with unusable content was already billed.

**Trade-offs.** These are application-level limits, not provider billing limits - also set a cap in the
DeepSeek console. Prices must be re-verified against the provider documentation when they change.
