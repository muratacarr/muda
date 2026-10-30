# ADR 0002: The AI proposes, the application decides

Status: accepted

Telegram text and model output are untrusted.

**Decision.** The model can only return JSON that validates against a discriminated union of two
operations (`update_menu_price`, `update_design`). Schemas forbid unknown keys and use closed vocabularies
(design) or bounded `Decimal`s (price). The model gets no tools, no database, no filesystem and no URL
access. Design values reach the browser only through explicit literal Tailwind classes and Ant Design theme
tokens; the frontend falls back to defaults for unknown values.

**Why.** Prompt-injection defenses based on keyword filters are bypassable. A strict boundary makes
"ignore previous instructions" harmless: the worst outcome is a rejected proposal.

**Trade-offs.** Less flexible than free-form editing. New capabilities (Phase 2+) mean adding explicit
operations, which is the intended friction.
