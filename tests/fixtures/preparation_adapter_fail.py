#!/usr/bin/env python3
"""Test preparation adapter that always exits with code 1."""

import sys

print("intentional failure", file=sys.stderr)
sys.exit(1)
