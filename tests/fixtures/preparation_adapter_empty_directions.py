#!/usr/bin/env python3
"""Test preparation adapter that returns empty directions list."""

import json
import sys

json.dump({"directions": []}, sys.stdout)
