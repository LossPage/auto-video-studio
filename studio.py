#!/usr/bin/env python3
"""Auto Video Studio — automatic YouTube video production. Start with:  python studio.py kurulum  (or: setup)"""
import sys

from studio.cli import main

if __name__ == "__main__":  # the guard is required for multiprocessing on Windows/macOS
    sys.exit(1 if main() is False else 0)
