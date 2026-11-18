#!/usr/bin/env python3
"""Offline lexical and delimiter checks for generated Cadence SKILL source.

This intentionally does not claim to replace the Cadence SKILL compiler.  It
blocks common generated-source failures (unbalanced delimiters, unterminated
strings, and unterminated block comments) before a live Bridge load is tried.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSE_TO_OPEN = {value: key for key, value in OPEN_TO_CLOSE.items()}


def lint_text(text: str, source: str = "<memory>") -> Dict[str, Any]:
    stack: List[tuple[str, int, int]] = []
    errors: List[Dict[str, Any]] = []
    line = 1
    column = 0
    index = 0
    in_string = False
    string_start = (0, 0)
    in_block_comment = False
    block_start = (0, 0)

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        column += 1

        if char == "\n":
            line += 1
            column = 0
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                column += 1
            else:
                index += 1
            continue
        if in_string:
            if char == "\\":
                index += 2
                column += 1
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == ";":
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            block_start = (line, column)
            index += 2
            column += 1
            continue
        if char == '"':
            in_string = True
            string_start = (line, column)
            index += 1
            continue
        if char in OPEN_TO_CLOSE:
            stack.append((char, line, column))
        elif char in CLOSE_TO_OPEN:
            if not stack:
                errors.append({"type": "unexpected_closer", "token": char, "line": line, "column": column})
            else:
                opener, open_line, open_column = stack.pop()
                if CLOSE_TO_OPEN[char] != opener:
                    errors.append(
                        {
                            "type": "mismatched_delimiter",
                            "opener": opener,
                            "closer": char,
                            "line": line,
                            "column": column,
                            "open_line": open_line,
                            "open_column": open_column,
                        }
                    )
        index += 1

    if in_string:
        errors.append({"type": "unterminated_string", "line": string_start[0], "column": string_start[1]})
    if in_block_comment:
        errors.append({"type": "unterminated_block_comment", "line": block_start[0], "column": block_start[1]})
    for opener, open_line, open_column in reversed(stack):
        errors.append(
            {
                "type": "unclosed_delimiter",
                "token": opener,
                "expected": OPEN_TO_CLOSE[opener],
                "line": open_line,
                "column": open_column,
            }
        )
    return {"source": source, "status": "passed" if not errors else "failed", "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check generated Cadence SKILL files for lexical and delimiter errors before live load."
    )
    parser.add_argument("files", nargs="+", help="one or more generated .il/.ils files")
    parser.add_argument("--json", action="store_true", help="emit one JSON array")
    args = parser.parse_args()

    results = []
    for raw_path in args.files:
        path = Path(raw_path)
        try:
            results.append(lint_text(path.read_text(encoding="utf-8"), str(path)))
        except OSError as exc:
            results.append(
                {"source": str(path), "status": "failed", "errors": [{"type": "read_failed", "error": str(exc)}]}
            )
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            print(f"{result['status']}: {result['source']}")
            for error in result["errors"]:
                print(f"  {error}")
    return 0 if all(item["status"] == "passed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
