"""
Sonnet-class per-token pricing ESTIMATES -- verify against the Console
pricing page (platform.claude.com) before relying on these for real spend.
"""

INPUT_PER_MTOK = 2.00
OUTPUT_PER_MTOK = 10.00
CACHE_READ_PER_MTOK = 0.20
# Cache-creation per-token price isn't pinned here (not in CLAUDE.md's cost
# rules); counted at the input-token rate as a conservative placeholder.
CACHE_CREATION_PER_MTOK = INPUT_PER_MTOK

COST_NOTE = (
    "ESTIMATE: Sonnet-class rates ($2/$10/$0.20 per MTok input/output/cache-read); "
    "cache-creation tokens billed at the input rate as a placeholder (its price isn't "
    "pinned here). Verify against the Console pricing page before relying on this for "
    "real spend."
)


def estimate_cost(usage):
    """`usage`: dict with input_tokens/output_tokens/cache_read_input_tokens/cache_creation_input_tokens."""
    return (
        usage["input_tokens"] / 1_000_000 * INPUT_PER_MTOK
        + usage["output_tokens"] / 1_000_000 * OUTPUT_PER_MTOK
        + usage["cache_read_input_tokens"] / 1_000_000 * CACHE_READ_PER_MTOK
        + usage["cache_creation_input_tokens"] / 1_000_000 * CACHE_CREATION_PER_MTOK
    )
