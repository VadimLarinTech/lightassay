#!/usr/bin/env python3
"""Test adapter: echoes input and includes received workflow config fields in parsed_response."""

import json
import sys

request = json.load(sys.stdin)
response = {
    "raw_response": "Echo: " + request["input"],
    "parsed_response": {
        "echoed": request["input"],
        "workflow_id": request["workflow_id"],
        "provider": request.get("provider"),
        "model": request.get("model"),
    },
    "usage": {
        "input_tokens": len(request["input"].split()),
        "output_tokens": len(request["input"].split()) + 1,
    },
}
json.dump(response, sys.stdout)
