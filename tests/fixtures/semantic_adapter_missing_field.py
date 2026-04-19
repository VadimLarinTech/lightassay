#!/usr/bin/env python3
"""Test semantic adapter: outputs JSON missing the required analysis_markdown field."""

import json
import sys

json.dump({"summary": "this field name is wrong"}, sys.stdout)
