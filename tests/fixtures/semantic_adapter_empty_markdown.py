#!/usr/bin/env python3
"""Test semantic adapter: outputs empty analysis_markdown."""

import json
import sys

json.dump({"analysis_markdown": "   "}, sys.stdout)
