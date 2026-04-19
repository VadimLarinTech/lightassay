#!/usr/bin/env python3
"""Stub workflow adapter — pretends to be the system under evaluation.

`lightassay` speaks a JSON-in / JSON-out protocol over stdin/stdout with the
workflow adapter. This stub ignores the input semantically and returns a
deterministic response. Replace it with a real adapter that calls your LLM
or workflow.
"""

import json
import sys


def main() -> None:
    request = json.load(sys.stdin)
    text = request.get("input", "")
    response = {
        "raw_response": f"STUB(ok): {text}",
        "parsed_response": {"verdict": "ok", "echo": text},
        "usage": {
            "input_tokens": max(1, len(text.split())),
            "output_tokens": max(1, len(text.split()) + 2),
        },
    }
    json.dump(response, sys.stdout)


if __name__ == "__main__":
    main()
