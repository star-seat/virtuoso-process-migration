#!/usr/bin/env python3
"""Verify normalized pre/post migration snapshots offline."""

from __future__ import annotations

import argparse
import sys

from migration_core import MigrationDataError, read_json, verify_migration, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare endpoint topology, interfaces, views, references, and readbacks without writing Virtuoso data.")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--post", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = verify_migration(read_json(args.audit), read_json(args.plan), read_json(args.post))
        write_json(args.output, result)
    except (OSError, ValueError, MigrationDataError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 2
    print(f"verification {result['status']}: {len(result['errors'])} error(s), {len(result['warnings'])} warning(s)")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

