"""Shared fixtures for inverter-dashboard tests."""

import os
import sys

# Make the package importable during test collection
SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(SRC))
