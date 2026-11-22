"""playcal ingest: read gameplay events (JSONL files or pasted PC0 blobs),
keep the ones that match the schema, write the rest to rejected.jsonl with a reason."""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Iterable, Iterator

from jsonschema import Draft202012Validator, FormatChecker

from .convert import FORMATS, convert
from .registry import Module

PASTE_PREFIX = "PC0:"
_PASTE_RE = re.compile(r"PC0:([A-Za-z0-9_-]+=*)")


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    try:
        text = files("playcal").joinpath("schema/event-v0.json").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):  # editable/dev checkout: schema lives at repo root
        text = (Path(__file__).resolve().parents[2] / "schema" / "event-v0.json").read_text(encoding="utf-8")
    return Draft202012Validator(json.loads(text), format_checker=FormatChecker())


def encode_paste(events: Iterable[dict]) -> str:
    """What the in-game "send result" button produces: one short line to paste anywhere."""
    raw = "\n".join(json.dumps(e, separators=(",", ":"), ensure_ascii=False) for e in events).encode()
    return PASTE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode()


def decode_paste(text: str) -> Iterator[tuple[str, str]]:
    """Yield (source, jsonl_line) for every PC0 blob found in free text (e.g. a WhatsApp export)."""
    for i, m in enumerate(_PASTE_RE.finditer(text), 1):
        try:
            raw = zlib.decompress(base64.urlsafe_b64decode(m.group(1))).decode()
        except (ValueError, zlib.error) as e:
            yield f"paste#{i}", json.dumps({"__decode_error__": str(e)})
            continue
        for line in raw.splitlines():
            yield f"paste#{i}", line


@dataclass
class Result:
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def check_lines(lines: Iterable[tuple[str, str]]) -> Result:
    res = Result()
    for n, (source, line) in enumerate(lines, 1):
        if not line.strip():
            continue
        where = f"{source}:{n}"
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            res.rejected.append({"where": where, "reason": f"not JSON: {e.msg}", "line": line})
            continue
        if isinstance(ev, dict) and "__decode_error__" in ev:
            res.rejected.append({"where": where, "reason": f"paste blob broken: {ev['__decode_error__']}", "line": None})
            continue
        errors = sorted(validator().iter_errors(ev), key=lambda e: list(e.path))
        if errors:
            reason = "; ".join(f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:3])
            res.rejected.append({"where": where, "reason": reason, "line": line})
        else:
            res.accepted.append(ev)
    return res


def _read_inputs(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    fmt = getattr(args, "source_format", None)
    if fmt:
        yield from convert(_raw_lines(args), fmt, args.game, args.game_version)
    else:
        yield from _raw_lines(args)


def _raw_lines(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    for path in args.files:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        name = "stdin" if path == "-" else path
        if args.paste:
            yield from decode_paste(text)
        else:
            for line in text.splitlines():
                yield name, line


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+", help="JSONL files, or '-' for stdin")
    p.add_argument("--paste", action="store_true", help="inputs are free text containing PC0: blobs")
    p.add_argument("--from", dest="source_format", choices=FORMATS,
                   help="inputs are an analytics export (one JSON row per line); level events become playcal events")
    p.add_argument("--game", help="with --from: game name to use instead of the one in the export")
    p.add_argument("--game-version", help="with --from: game version (the exports often lack one; default: unknown)")
    p.add_argument("--out", default="accepted.jsonl", help="where valid events go (default: accepted.jsonl)")
    p.add_argument("--rejected", default="rejected.jsonl", help="where invalid lines go, with a reason")
    p.add_argument("--json", action="store_true", help="print a JSON summary")


def _run(args: argparse.Namespace) -> int:
    try:
        res = check_lines(_read_inputs(args))
    except OSError as e:
        print(f"playcal ingest: {e}", file=sys.stderr)
        return 2
    Path(args.out).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in res.accepted), encoding="utf-8")
    Path(args.rejected).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in res.rejected), encoding="utf-8")
    summary = {"accepted": len(res.accepted), "rejected": len(res.rejected), "out": args.out, "rejected_file": args.rejected,
               "reasons": [f"{r['where']}: {r['reason']}" for r in res.rejected[:10]]}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"{summary['accepted']} accepted -> {args.out}, {summary['rejected']} rejected -> {args.rejected}")
        for r in summary["reasons"]:
            print(f"  {r}")
    return 0


MODULE = Module(name="ingest", summary="validate gameplay events (JSONL or pasted PC0 blobs) against the event schema",
                add_args=_add_args, run=_run)
