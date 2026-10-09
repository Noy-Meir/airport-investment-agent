"""
Run one agent turn through src.agent.respond.respond and print the answer,
the tools called, and token usage + estimated cost.

Usage:
    python scripts/ask.py "How does BOS look as an investment?"
    python scripts/ask.py --rules "How does BOS look as an investment?"

With no API key configured, falls back to the built-in rules interpreter
automatically (LLM_PROVIDER=auto). --rules forces the rules interpreter and
never makes a billed API call.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.respond import Session, respond


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--rules", action="store_true", help="Force the built-in rules interpreter; no API call.")
    args = parser.parse_args()

    session = Session()
    result = respond(session, args.question, provider_override="rules" if args.rules else None)

    print(f"=== Answer (mode: {result['mode']}) ===")
    print(result["answer"])

    if result.get("notice"):
        print("\n=== Notice ===")
        print(result["notice"])

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
