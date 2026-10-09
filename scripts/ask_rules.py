"""
Run one or more questions through the deterministic rules router
(src.agent.rules_router.answer) against the real data/cache.db, sharing one
State across questions so each later argument can be a follow-up to the one
before it.

No Anthropic API call, no API key needed, no network access -- this is the
non-LLM path.

Usage:
    python scripts/ask_rules.py "question one" ["follow-up question" ...]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cache import db
from src.agent.rules_router import answer


def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/ask_rules.py "question" ["follow-up question" ...]')
        sys.exit(1)

    questions = sys.argv[1:]
    conn = db.connect()
    state = None
    try:
        for i, question in enumerate(questions, start=1):
            print(f"=== Turn {i}: {question!r} ===")
            t0 = time.perf_counter()
            result = answer(question, state=state, conn=conn)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            state = result["state"]

            print("\n--- Answer ---")
            print(result["answer"])

            print("\n--- Tools called ---")
            if not result["trace"]:
                print("(none)")
            for call in result["trace"]:
                print(f"- {call['tool']}({call['args']}) [{call['ms']:.1f}ms]")

            print(f"\n--- Mode: {result['mode']} | turn elapsed: {elapsed_ms:.1f}ms ---\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
