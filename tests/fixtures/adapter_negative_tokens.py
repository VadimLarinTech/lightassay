#!/usr/bin/env python3
"""Test adapter: returns negative input_tokens to test rejection."""

import json
import sys

request = json.load(sys.stdin)
response = {
    "raw_response": "Echo: " + request["input"],
    "parsed_response": None,
    "usage": {
        "input_tokens": -1,
        "output_tokens": 5,
    },
}
json.dump(response, sys.stdout)
