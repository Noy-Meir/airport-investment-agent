"""
Run one agent turn against the real Anthropic API and print the answer,
the tools called, and token usage + estimated cost.

Usage:
    python scripts/ask.py "How does BOS look as an investment?"

Requires ANTHROPIC_API_KEY and MODEL_NAME (env or .env at project root).
Not auto-run by anything -- this makes a real, billed API call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.agent import run_turn


def main():
    if len(sys.argv) != 2:
        print('Usage: python scripts/ask.py "your question"')
        sys.exit(1)

    question = sys.argv[1]
    result = run_turn([], question)

    print("=== Answer ===")
    print(result["answer"])

    print("\n=== Tools called ===")
    if not result["trace"]:
        print("(none)")
    for call in result["trace"]:
        print(f"- {call['tool']}({call['args']}) [{call['ms']:.1f}ms]")

    usage = result["usage"]
    print("\n=== Usage ===")
    print(f"input_tokens: {usage['input_tokens']}")
    print(f"output_tokens: {usage['output_tokens']}")
    print(f"cache_read_input_tokens: {usage['cache_read_input_tokens']}")
    print(f"cache_creation_input_tokens: {usage['cache_creation_input_tokens']}")
    print(f"estimated_cost_usd: ${usage['estimated_cost_usd']:.6f}  ({usage['cost_note']})")


if __name__ == "__main__":
    main()
