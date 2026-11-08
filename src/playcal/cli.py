from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, surfaces
from .registry import load_modules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="playcal", description="playcal: one toolkit for AI agents building games.")
    p.add_argument("--version", action="version", version=f"playcal {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("modules", help="list installed modules")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=_list_modules)

    sub.add_parser("mcp", help="serve every module as an MCP tool over stdio").set_defaults(
        func=lambda a: surfaces.serve())
    sk = sub.add_parser("skill", help="print a SKILL.md for coding agents, built from the installed modules")
    sk.add_argument("--project", metavar="DIR", help="write .claude/skills/playcal/SKILL.md into this game project, "
                    "with its engine and red gate first")
    sk.add_argument("--engine", choices=sorted(surfaces.PROJECT_GUIDE), help="with --project: skip engine detection")
    sk.set_defaults(func=_skill)

    for mod in load_modules():
        text = mod.summary.replace("%", "%%")  # argparse treats % as a format code
        sp = sub.add_parser(mod.name, help=text, description=text)
        mod.add_args(sp)
        sp.set_defaults(func=mod.run)
    return p


def _skill(args: argparse.Namespace) -> int:
    if not args.project:
        print(surfaces.skill_md())
        return 0
    engine = args.engine or surfaces.detect_engine(args.project)
    if not engine:
        print(f"playcal skill: no Unity (Assets/ + ProjectSettings/) or Godot (project.godot) project in {args.project}; "
              "pass --engine", file=sys.stderr)
        return 2
    out = Path(args.project) / ".claude" / "skills" / "playcal" / "SKILL.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(surfaces.skill_md(engine), encoding="utf-8")
    print(f"wrote {out} ({engine})")
    return 0


def _list_modules(args: argparse.Namespace) -> int:
    mods = load_modules()
    if args.json:
        print(json.dumps([{"name": m.name, "summary": m.summary, "engines": list(m.engines)} for m in mods]))
    else:
        for m in mods:
            print(f"{m.name:12} {m.summary}")
    return 0


def parse(argv: list[str]) -> argparse.Namespace:
    """Like parse_args, but what follows -- always lands in the module's `params`. argparse before
    3.12 leaves it unparsed when a nargs="*" positional was already filled (empty) earlier."""
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if extra[0] != "--" or not hasattr(args, "params"):
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
        args.params = [*args.params, *extra[1:]]
    return args


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:  # a cp1254 console can't print every file name
            reconfigure(errors="replace")
    args = parse(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
