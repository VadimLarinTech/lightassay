#!/usr/bin/env python3
"""Test preparation adapter that returns JSON missing required fields."""

import json
import sys

json.dump({"unexpected_key": "value"}, sys.stdout)
