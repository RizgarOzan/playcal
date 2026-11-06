"""playcal snap: save a JSON read (a hierarchy, a component dump, game state) under a name, and diff
two saves, so "what did this run change" is a short list instead of two big dumps.

Unity's instanceIds only hold within one Editor session: reopen the scene, restart the Editor, or
compare edit mode with play mode and they differ, so a plain diff says "everything removed,
everything added". snap ignores those
ids by default and matches list items by hierarchyPath, then path, guid, name; two siblings with
the same name become name=Enemy and name=Enemy#2.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from . import checks
from .registry import Module

# ids change per Editor session; isDirty flips on every edit and only says "unsaved"
VOLATILE = ("instanceId", "instanceID", "isDirty")
STABLE_KEYS = ("hierarchyPath", "path", "guid", "name", "id")


class SnapError(Exception):
    pass


def _file(folder: Path, name: str) -> Path:
    if not re.fullmatch(r"[\w.-]+", name):
        raise SnapError(f"snapshot names are letters, digits, . _ - only: {name!r}")
    return folder / f"{name}.json"


def save(folder: Path, name: str, data, source: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    path = _file(folder, name)
    text = json.dumps({"source": source, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "data": data},
                      ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return {"name": name, "file": str(path), "bytes": len(text)}


def load(folder: Path, ref: str):
    """A saved name, or a path to any JSON file (a raw dump works too)."""
    path = Path(ref)
    if not (path.suffix == ".json" and path.exists()):
        path = _file(folder, ref)
    if not path.exists():
        raise SnapError(f"no snapshot {ref!r} in {folder}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["data"] if isinstance(doc, dict) and set(doc) >= {"source", "data"} else doc


def list_(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"name": f.stem, "source": doc.get("source"), "saved_at": doc.get("saved_at"),
                    "bytes": f.stat().st_size})
    return out


def compare(a, b, ignore=VOLATILE, limit: int = 50) -> dict:
    changes = checks.diff(a, b, limit=limit, ignore=ignore, id_keys=STABLE_KEYS)
    rows = [c for c in changes if "path" in c]  # the last entry may be {"truncated": n}
    return {"same": not changes, "added": sum("added" in c for c in rows),
            "removed": sum("removed" in c for c in rows),
            "changed": sum("before" in c for c in rows), "changes": changes}


def _read_unity(cmd: str, project: str | None):
    from . import engines
    eng = engines.get()
    ready = eng.ready(project, timeout=60, stable=1)  # a read: a flicker fails loudly
    if not ready["settled"]:
        raise SnapError(f"Editor not ready: {ready['reason']}. {ready.get('hint', '')}".strip())
    res = eng.run(shlex.split(cmd), project)
    if not res["success"]:
        raise SnapError(f"{cmd} failed: {json.dumps(res['errors'], ensure_ascii=False)}")
    return res["data"]


def _read_godot(project: str | None, scene: str | None) -> dict:
    from .engines import godot
    try:
        return godot.Godot().tree(project, scene)["tree"]
    except RuntimeError as e:
        raise SnapError(f"godot: {e}") from e


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["save", "diff", "list"],
                   help="save NAME: store a read. diff A [B]: what changed from A to B (or to a live --unity read). list")
    p.add_argument("refs", nargs="*", help="save: NAME [FILE or - for stdin]. diff: A [B], each a saved name or a .json file")
    p.add_argument("--unity", metavar="CMD", help='read from the Editor, e.g. "scene get_hierarchy"')
    p.add_argument("--godot", action="store_true", help="read the node tree of the Godot main scene (one headless frame)")
    p.add_argument("--scene", help="with --godot: scene to read instead of the main scene (res://...)")
    p.add_argument("--project-path", help="Unity project folder with --unity, Godot project folder with --godot")
    p.add_argument("--dir", default=".playcal/snaps", help="where saves live (default .playcal/snaps)")
    p.add_argument("--ignore", action="append", default=None, metavar="KEY",
                   help="field to leave out of the diff, repeatable (default: instanceId). --ignore '' keeps everything")
    p.add_argument("--limit", type=int, default=50, help="most changes to list (default 50)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def _run(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    out: Any
    try:
        if args.action == "list":
            out = list_(folder) if folder.exists() else []
        elif args.action == "save":
            if not args.refs:
                raise SnapError("give a name: playcal snap save NAME")
            if args.godot:
                data, source = _read_godot(args.project_path, args.scene), "godot tree"
            elif args.unity:
                data, source = _read_unity(args.unity, args.project_path), f"unity {args.unity}"
            else:
                src = args.refs[1] if len(args.refs) > 1 else "-"
                text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
                data, source = json.loads(text), "stdin" if src == "-" else src
            out = save(folder, args.refs[0], data, source)
        else:
            if not args.refs or (len(args.refs) == 1 and not (args.unity or args.godot)):
                raise SnapError("diff needs A and B, or A and --unity CMD for a live read")
            a = load(folder, args.refs[0])
            b = (load(folder, args.refs[1]) if len(args.refs) > 1 else _read_godot(args.project_path, args.scene)
                 if args.godot else _read_unity(args.unity, args.project_path))
            ignore = VOLATILE if args.ignore is None else [k for k in args.ignore if k]
            out = compare(a, b, ignore, args.limit)
    except (SnapError, OSError, json.JSONDecodeError) as e:
        print(f"playcal snap: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    elif args.action == "diff":
        for c in out["changes"]:
            print(json.dumps(c, ensure_ascii=False))
        print("no changes" if out["same"] else
              f"{out['added']} added, {out['removed']} removed, {out['changed']} changed")
    elif args.action == "list":
        for s in out:
            print(f"{s['name']}  {s['saved_at']}  {s['source']}  {s['bytes']} bytes")
    else:
        print(f"saved {out['name']} ({out['bytes']} bytes) to {out['file']}")
    return 1 if args.action == "diff" and not out["same"] else 0


MODULE = Module(name="snap", summary="save a JSON read under a name and diff two saves, with ids that change between sessions left out",
                add_args=_add_args, run=_run)
