#!/usr/bin/env python3
"""Test adapter: outputs JSON with missing required fields."""

import json
import sys

json.dump({"raw_response": "hello"}, sys.stdout)
