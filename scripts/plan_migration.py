#!/usr/bin/env python3
"""Build a deterministic offline Virtuoso migration plan."""

from __future__ import annotations

import argparse
import sys

from migration_core import MigrationDataError, build_plan, read_json, write_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Scan a normalized read-only audit, batch conflicts, and produce a blocked or ready migration plan. This tool never writes Virtuoso data."
    )
    result.add_argument("--audit", required=True, help="normalized audit JSON")
    result.add_argument("--profile", required=True, help="process mapping profile JSON")
    result.add_argument("--decisions", help="optional explicit overwrite/rename/reuse decisions JSON")
    result.add_argument("--output", required=True, help="output plan JSON")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        audit = read_json(args.audit)
        profile = read_json(args.profile)
        decisions = read_json(args.decisions) if args.decisions else None
        plan = build_plan(audit, profile, decisions)
        write_json(args.output, plan)
    except (OSError, ValueError, MigrationDataError) as exc:
        print(f"planning failed: {exc}", file=sys.stderr)
        return 2
    print(f"{plan['status']}: {len(plan['conflicts'])} conflict(s), {len(plan['blockers'])} blocker(s)")
    return 0 if plan["status"] == "ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())

