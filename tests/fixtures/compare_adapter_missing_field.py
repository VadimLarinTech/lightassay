#!/usr/bin/env python3
"""Test compare adapter: outputs JSON missing the required compare_markdown field."""

import json
import sys

json.dump({"summary": "this field name is wrong"}, sys.stdout)
