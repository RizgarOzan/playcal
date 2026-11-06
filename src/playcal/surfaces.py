"""The MCP server and SKILL.md, both generated from the module registry so a module is written once.

Each module's argparse arguments become a JSON Schema; a tool call becomes the same argv the CLI
would get, run in-process with --json, and the printed JSON is the tool result.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys

from . import __version__
from .registry import Module, load_modules

SKIP = {"help", "json"}
PROTOCOL = "2025-06-18"


def _parser(mod: Module) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"playcal {mod.name}", add_help=False)
    mod.add_args(p)
    return p


def _actions(mod: Module) -> list[argparse.Action]:
    return [a for a in _parser(mod)._actions if a.dest not in SKIP]


def _prop(a: argparse.Action) -> dict:
    if isinstance(a, argparse._StoreTrueAction):
        prop: dict = {"type": "boolean"}
    elif isinstance(a, argparse._AppendAction) or a.nargs in ("*", "+"):
        prop = {"type": "array", "items": {"type": "string"}}
    else:
        prop = {"type": {int: "integer", float: "number"}.get(a.type, "string")}
    if a.choices:
        prop["enum"] = list(a.choices)
    if a.help:
        prop["description"] = a.help.replace("%%", "%")
    if a.default not in (None, False, [], argparse.SUPPRESS):
        prop["default"] = a.default
    return prop


def tool_schema(mod: Module) -> dict:
    props, required = {}, []
    for a in _actions(mod):
        props[a.dest] = _prop(a)
        if not a.option_strings and a.nargs not in ("?", "*"):
            required.append(a.dest)
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def argv_for(mod: Module, arguments: dict) -> list[str]:
    """Tool arguments -> CLI argv. Options first, then single positionals, then list positionals after --."""
    opts, singles, lists = [], [], []
    for a in _actions(mod):
        v = arguments.get(a.dest)
        if v is None:
            continue
        if a.option_strings:
            flag = a.option_strings[-1]
            if isinstance(a, argparse._StoreTrueAction):
                opts += [flag] if v else []
            elif isinstance(a, argparse._AppendAction):
                for item in v:
                    opts += [flag, str(item)]
            else:
                opts += [flag, str(v)]
        elif a.nargs in ("*", "+"):
            lists += [str(x) for x in v]
        else:
            singles.append(str(v))
    return [*opts, *singles, *(["--", *lists] if lists else [])]


def call(mod: Module, arguments: dict) -> tuple[int, str]:
    """Runs the module like the CLI would, with --json, and returns (exit code, captured output)."""
    from .cli import parse

    out, err = io.StringIO(), io.StringIO()
    argv = [mod.name, *argv_for(mod, arguments)]
    if any(a.dest == "json" for a in _parser(mod)._actions):
        argv.insert(1, "--json")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = parse(argv)
            rc = args.func(args)
        except SystemExit as e:  # argparse rejects bad arguments this way
            rc = e.code if isinstance(e.code, int) else 2
    text = out.getvalue().strip() or err.getvalue().strip()
    return rc, text


def _tool(mod: Module) -> dict:
    return {"name": f"playcal_{mod.name}", "description": mod.summary, "inputSchema": tool_schema(mod)}


def handle(msg: dict, mods: dict[str, Module]) -> dict | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "playcal", "version": __version__},
                  "instructions": "playcal tools, which when (`playcal X` below is the tool playcal_X):\n" + "\n".join(GUIDE)}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [_tool(m) for m in mods.values()]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        mod = mods.get(str(params.get("name", "")).removeprefix("playcal_"))
        if mod is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
        rc, text = call(mod, params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}], "isError": rc != 0}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None) -> int:
    """MCP over stdio: one JSON-RPC message per line."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    mods = {m.name: m for m in load_modules()}
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg, mods)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


GUIDE = [  # one line per situation: shown at the top of SKILL.md and sent as MCP `instructions`
    "- Before anything in a Unity project: `playcal doctor`. It says whether the CLI and an open Editor are reachable.",
    "- Any Editor write (create, set_field, save_scene, recompile): `playcal loop run NAME -- ARGS` with `--verify` and `--expect`. "
    "Never call `unity command` directly for a write; a success during a domain reload is not a landed change.",
    "- Scripts changed: `playcal loop run ... --compile`, then `playcal logs --since \"Reloading assemblies\"` for the first cause.",
    "- Something is red in the console: `playcal logs` first, it collapses repeats and names the first cause.",
    "- Big command output: `playcal query` to keep only the fields you need instead of reading the whole hierarchy.",
    "- Moved, renamed, copied or deleted files under Assets/ outside the Editor: `playcal meta` before Unity imports them.",
    "- Before a risky step: `playcal checkpoint save`; `playcal loop run --checkpoint` does it for you.",
    "- Does it still run: `playcal observe` (press Play, diff the scene, collect errors), `playcal visual check` for a screenshot diff.",
    "- Playtest data: `ingest` validates, `report` summarizes, `calibrate` predicts human pass rates from bot runs.",
]


# what `playcal skill --project` puts first for each engine: the check to pass before calling a change done
PROJECT_GUIDE = {
    "unity": ["- This is a Unity project. Start with `playcal doctor`; do it before the first Editor command.",
              "- Red gate: after any change, `playcal loop settle --compile` must exit 0 and `playcal observe` must say clean "
              "before you report the change as done."],
    "godot": ["- This is a Godot project. playcal runs Godot headless; set PLAYCAL_GODOT if `godot` is not on PATH.",
              "- Red gate: after any change, `playcal loop settle --engine godot` must exit 0 (no parse errors) and "
              "`playcal observe --engine godot` must say clean before you report the change as done.",
              "- Flaky bug: `playcal playtest --engine godot` runs it three times; inconclusive (exit 7) means not fixed."],
}


def detect_engine(project: str | Path) -> str | None:
    d = Path(project)
    if (d / "project.godot").exists():
        return "godot"
    if (d / "Assets").is_dir() and (d / "ProjectSettings").is_dir():
        return "unity"
    return None


def skill_md(engine: str | None = None) -> str:
    desc = ("Game-dev toolkit for agents. Use to read playtest results, summarize Unity logs, "
            "and run Unity Editor commands safely (wait until ready, run, verify). Every command takes --json.")
    first: list[str] = []
    if engine:
        desc = (f"Use before and after every change in this {engine.capitalize()} project. playcal checks that it "
                f"compiles and runs clean (the red gate) before a change is called done. Every command takes --json.")
        first = ["## This project", "", *PROJECT_GUIDE[engine], ""]
    lines = ["---", "name: playcal", f"description: {desc}",
             "---", "", "# playcal", "",
             "Run `playcal <command> --json` and read the JSON. Exit code 0 means ok.", "",
             *first, "## Which command when", "", *GUIDE, ""]
    for mod in load_modules():
        lines += [f"## playcal {mod.name}", "", mod.summary + ".", ""]
        for a in _actions(mod):
            name = a.option_strings[-1] if a.option_strings else a.dest
            lines.append(f"- `{name}`: {(a.help or '').replace('%%', '%')}")
        lines.append("")
    return "\n".join(lines)
