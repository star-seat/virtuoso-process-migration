#!/usr/bin/env python3
"""Validate a ready plan before any live migration work."""

from __future__ import annotations

import argparse
import sys

from migration_core import MigrationDataError, read_json, validate_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject unsafe, blocked, or internally inconsistent migration plans.")
    parser.add_argument("plan", help="plan JSON produced by plan_migration.py")
    args = parser.parse_args()
    try:
        errors = validate_plan(read_json(args.plan))
    except (OSError, ValueError, MigrationDataError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("plan is ready and passes offline safety validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

