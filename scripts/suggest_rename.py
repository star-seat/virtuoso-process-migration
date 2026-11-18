#!/usr/bin/env python3
"""Suggest collision-free names from a frozen read-only target inventory."""

from __future__ import annotations

import argparse
import json
import sys

from migration_core import MigrationDataError, read_json, suggest_rename_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Suggest target cell names. The chosen name must still be re-scanned by plan_migration.py.")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--target-library", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--reserved", action="append", default=[], help="additional planned name to avoid; repeatable")
    args = parser.parse_args()
    try:
        candidates = suggest_rename_candidates(
            read_json(args.audit), args.target_library, args.base, args.count, args.reserved
        )
    except (OSError, ValueError, MigrationDataError) as exc:
        print(f"suggestion failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"target_library": args.target_library, "candidates": candidates}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

