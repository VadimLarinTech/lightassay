#!/usr/bin/env python3
"""Test adapter: echoes the input back as raw_response."""

import json
import sys

request = json.load(sys.stdin)
response = {
    "raw_response": "Echo: " + request["input"],
    "parsed_response": {"echoed": request["input"]},
    "usage": {
        "input_tokens": len(request["input"].split()),
        "output_tokens": len(request["input"].split()) + 1,
    },
}
json.dump(response, sys.stdout)
