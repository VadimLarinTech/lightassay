#!/usr/bin/env python3
"""Test compare adapter: outputs empty compare_markdown."""

import json
import sys

json.dump({"compare_markdown": "   "}, sys.stdout)
