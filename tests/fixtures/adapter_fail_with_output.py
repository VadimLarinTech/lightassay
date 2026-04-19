#!/usr/bin/env python3
"""Test adapter: writes diagnostic output to stdout then fails with non-zero exit."""

import sys

print("diagnostic: adapter encountered an error during processing")
print("detail: input validation failed for case")
sys.exit(1)
