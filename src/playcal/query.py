"""playcal query: cut a big JSON result (a hierarchy dump, a component list) down to the fields you asked for.

A full scene dump can be tens of thousands of tokens; usually an agent needs a few names and one
field. query reads JSON from a file, stdin or a Unity read command, picks a list (--from), keeps
items that pass --where (same syntax as loop --expect), and returns only --select fields.
It reports bytes in and out so the saving is visible.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Sequence

from . import checks
from .registry import Module


def query(data, frm: str = "", where: Sequence[str] = (), select: Sequence[str] = (), limit: int = 50) -> dict:
    found = checks.lookup(data, frm)
    items = found[0] if len(found) == 1 and isinstance(found[0], list) else found
    kept = [it for it in items if all(checks.check(it, w)["ok"] for w in where)]
    rows = [_pick(it, select) for it in kept[:limit]] if select else kept[:limit]
    out = {"count": len(kept), "items": rows}
    if len(kept) > limit:
        out["truncated"] = len(kept) - limit
    return out


def _pick(item, select):
    row = {}
    for path in select:
        values = checks.lookup(item, path)
        row[path] = values if {"*", "**"} & set(path.split(".")) else values[0] if values else None
    return row


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", nargs="?", default="-", help="JSON file, or - for stdin (default). Ignored with --unity")
    p.add_argument("--unity", metavar="CMD", help='read from a Unity Editor command instead, e.g. "get_hierarchy --depth 3"')
    p.add_argument("--project-path", help="Unity project folder, with --unity")
    p.add_argument("--from", dest="frm", default="", metavar="PATH", help="the list to read, e.g. data.objects; ** walks nested children at any depth")
    p.add_argument("--where", action="append", default=[], metavar="EXPR",
                   help="keep items where EXPR holds: name~Enemy, active=true, tag!=Untagged. Repeatable")
    p.add_argument("--select", action="append", default=[], metavar="PATH", help="field to keep per item. Repeatable")
    p.add_argument("--limit", type=int, default=50, help="most items to return (default 50)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _load(args) -> tuple:
    if args.unity:
        from . import engines
        eng = engines.get()
        ready = eng.ready(args.project_path, timeout=60, stable=1)  # a read: a flicker fails loudly
        if not ready["settled"]:
            return None, ready
        res = eng.run(shlex.split(args.unity), args.project_path)
        if not res["success"]:
            return None, {"reason": "command_failed", "errors": res["errors"]}
        data = res["data"]
        return data, len(json.dumps(data, ensure_ascii=False))
    text = sys.stdin.read() if args.source == "-" else Path(args.source).read_text(encoding="utf-8")
    return json.loads(text), len(text)


def _run(args: argparse.Namespace) -> int:
    try:
        data, size = _load(args)
    except (OSError, json.JSONDecodeError) as e:
        print(f"playcal query: {e}", file=sys.stderr)
        return 2
    if data is None:
        print(json.dumps(size, ensure_ascii=False))
        return 3
    out = query(data, args.frm, args.where, args.select, args.limit)
    text = json.dumps(out, ensure_ascii=False)
    out["bytes_in"], out["bytes_out"] = size, len(text)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        for row in out["items"]:
            print(json.dumps(row, ensure_ascii=False))
        print(f"{out['count']} matched, {size} bytes in, {len(text)} out" +
              (f", {out['truncated']} not shown (raise --limit)" if "truncated" in out else ""))
    return 0


MODULE = Module(name="query", summary="pick a list, filter it and keep only the fields you need from a big JSON result",
                add_args=_add_args, run=_run)
