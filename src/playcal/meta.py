"""playcal meta: finds .meta and GUID damage before Unity imports it. Agents move, rename, copy and delete
files outside the Editor; Unity only notices on the next import, and by then a moved script has a new GUID
and every scene that used it says "Missing script". Reads files only, no Editor needed.

    playcal meta --project .            # exit 1 when something will break on import

Errors (will break): duplicate_guid (two .meta files, one GUID, usually a copied .meta), moved_without_meta
(a .meta left behind while its asset moved: same file name, one has no .meta, the other no asset),
bad_meta (no 32-hex guid line), missing_script (a scene/prefab/asset points at a script GUID no .meta has).
Warnings: missing_meta (new file, Unity writes one on import), orphan_meta, meta_case (matches only when case
is ignored), missing_ref (a non-script reference to an unknown GUID), class_name (a MonoBehaviour or
ScriptableObject whose class name isn't the file name, so it can't be attached).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from .registry import Module

GUID = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.M)
REF = re.compile(r"\{\s*fileID:\s*(-?\d+)\s*,\s*guid:\s*([0-9a-fA-F]{32})\s*,\s*type:\s*\d+\s*\}")
CLASS = re.compile(r"^\s*(?:(?:public|internal|sealed|partial|abstract)\s+)*class\s+(\w+)(\s*<)?\s*:\s*"
                   r"(?:UnityEngine\.)?(?:MonoBehaviour|ScriptableObject)\b", re.M)
ABSTRACT = re.compile(r"\babstract\s+(?:\w+\s+)*class\s+(\w+)")
BUILTIN = "0" * 16  # built-in resources (default materials, fonts) have no .meta anywhere


def _ignored(name: str) -> bool:  # what Unity's importer skips
    return name.startswith(".") or name.endswith("~") or name.lower() == "cvs" or name.endswith(".tmp")


def _roots(project: Path) -> list[Path]:
    """Assets/ plus embedded packages: what this project owns and Unity writes .meta files for."""
    pk = project / "Packages"
    emb = sorted(d for d in pk.iterdir() if d.is_dir() and (d / "package.json").exists()) if pk.is_dir() else []
    return [project / "Assets", *emb]


def _known_elsewhere(project: Path) -> tuple[list[Path], bool]:
    """Folders whose GUIDs count as present but aren't checked: the package cache and local file: packages.
    The bool says whether the cache was there to read."""
    dirs, cache = [], project / "Library" / "PackageCache"
    if cache.is_dir():
        dirs.append(cache)
    try:
        deps = json.loads((project / "Packages" / "manifest.json").read_text(encoding="utf-8")).get("dependencies", {})
    except (OSError, ValueError):
        deps = {}
    for v in deps.values():
        if isinstance(v, str) and v.startswith("file:"):
            p = (project / "Packages" / v[5:]).resolve()
            if p.is_dir():
                dirs.append(p)
    return dirs, cache.is_dir()


def _guid(meta: Path) -> str | None:
    try:
        m = GUID.search(meta.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m[1].lower() if m else None


def check(project: Path) -> dict:
    rel = lambda p: p.relative_to(project).as_posix()
    errors: list[dict] = []
    warnings: list[dict] = []
    owners: dict[str, list[str]] = defaultdict(list)
    no_meta: list[Path] = []
    orphans: list[Path] = []
    yaml_files: list[Path] = []
    scripts: list[Path] = []

    for root in _roots(project):
        if not root.is_dir():
            continue
        for d, subdirs, files in os.walk(root):
            subdirs[:] = [s for s in subdirs if not _ignored(s)]
            here = Path(d)
            names = [n for n in subdirs + files if not _ignored(n)]
            have = set(names)
            lower = {n.lower(): n for n in names}
            for n in names:
                if n.endswith(".meta"):
                    asset = n[:-5]
                    g = _guid(here / n)
                    if g is None:
                        errors.append({"kind": "bad_meta", "path": rel(here / n)})
                    else:
                        owners[g].append(rel(here / n))
                    if asset not in have:
                        if asset.lower() in lower:
                            warnings.append({"kind": "meta_case", "path": rel(here / n), "asset": lower[asset.lower()]})
                        else:
                            orphans.append(here / n)
                    continue
                if n + ".meta" not in have and (n + ".meta").lower() not in lower:
                    no_meta.append(here / n)
                if n in files:
                    if n.endswith(".cs"):
                        scripts.append(here / n)
                    else:
                        yaml_files.append(here / n)

    for g, paths in owners.items():
        if len(paths) > 1:
            errors.append({"kind": "duplicate_guid", "guid": g, "paths": sorted(paths)})

    # a .meta with no asset and an asset with no .meta, same file name: the asset moved without it
    left = defaultdict(list)
    for m in orphans:
        left[m.name[:-5].lower()].append(m)
    for a in no_meta:
        stays = left.get(a.name.lower())
        if stays:
            m = stays.pop(0)
            errors.append({"kind": "moved_without_meta", "path": rel(a), "meta": rel(m), "guid": _guid(m)})
            orphans.remove(m)
        else:
            warnings.append({"kind": "missing_meta", "path": rel(a)})
    warnings += [{"kind": "orphan_meta", "path": rel(m)} for m in orphans]

    known = set(owners)
    extra, packages_read = _known_elsewhere(project)
    for d in extra:
        for f in d.rglob("*.meta"):
            g = _guid(f)
            if g:
                known.add(g)

    for f in yaml_files:
        try:
            with f.open("rb") as fh:
                if fh.read(5) != b"%YAML":
                    continue  # binary-serialized or not a Unity asset at all
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits: dict[tuple[str, str], list[int]] = {}
        for i, line in enumerate(text.splitlines(), 1):
            for m in REF.finditer(line):
                g = m[2].lower()
                if g in known or g.startswith(BUILTIN):
                    continue
                kind = "missing_script" if "m_Script:" in line and m[1] == "11500000" else "missing_ref"
                hits.setdefault((kind, g), []).append(i)
        for (kind, g), lines in hits.items():
            item = {"kind": kind, "path": rel(f), "guid": g, "line": lines[0], "count": len(lines)}
            (errors if kind == "missing_script" and packages_read else warnings).append(item)

    for f in scripts:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        abstract = set(ABSTRACT.findall(text))
        classes = [m[1] for m in CLASS.finditer(text) if not m[2] and m[1] not in abstract]
        if classes and f.stem not in classes:
            warnings.append({"kind": "class_name", "path": rel(f), "classes": classes})

    counts: dict[str, int] = defaultdict(int)
    for x in errors + warnings:
        counts[x["kind"]] += 1
    return {"ok": not errors, "project": str(project), "packages_read": packages_read,
            "counts": dict(sorted(counts.items())), "errors": errors, "warnings": warnings}


WHY = {
    "duplicate_guid": "two .meta files share a GUID; Unity gives one a new GUID and its references break",
    "moved_without_meta": "moved without its .meta; Unity will give it a new GUID. Move the .meta next to it",
    "bad_meta": "no valid guid line",
    "missing_script": "script GUID not found; this will show as Missing script",
    "missing_ref": "reference to a GUID no .meta has",
    "missing_meta": "no .meta yet; Unity writes one on import (fine for a new file)",
    "orphan_meta": "its asset is gone",
    "meta_case": "matches its asset only when case is ignored",
    "class_name": "class name isn't the file name, so Unity can't attach it",
}


def _line(level: str, x: dict) -> str:
    where = ", ".join(x["paths"]) if "paths" in x else x["path"]
    extra = f" (meta: {x['meta']})" if "meta" in x else f" line {x['line']}" + (f", {x['count']}x" if x["count"] > 1 else "") \
        if "line" in x else f" ({', '.join(x['classes'])})" if "classes" in x else ""
    return f"{level} {x['kind']}: {where}{extra}: {WHY[x['kind']]}"


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", default=".", help="Unity project folder (the one with Assets/), default .")
    p.add_argument("--limit", type=int, default=20, help="text output: lines per kind (default 20, 0 = all)")
    p.add_argument("--json", action="store_true", help="print the result as JSON (always complete)")


def _run(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    if not (project / "Assets").is_dir():
        print(f"playcal meta: {project} has no Assets/ folder; pass --project", file=sys.stderr)
        return 2
    out = check(project)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["ok"] else 1
    shown: dict[str, int] = defaultdict(int)
    for level, items in (("error", out["errors"]), ("warning", out["warnings"])):
        for x in items:
            shown[x["kind"]] += 1
            if not args.limit or shown[x["kind"]] <= args.limit:
                print(_line(level, x))
    for k, n in out["counts"].items():
        if args.limit and n > args.limit:
            print(f"... {n - args.limit} more {k} (--limit 0 or --json for all)")
    if not out["packages_read"]:
        print("note: no Library/PackageCache yet (open the project once), so unknown script GUIDs are warnings")
    e, w = len(out["errors"]), len(out["warnings"])
    print(f"{'ok' if out['ok'] else 'broken'}: {e} error(s), {w} warning(s)")
    return 0 if out["ok"] else 1


MODULE = Module(name="meta", summary="find .meta and GUID damage (copied, moved or missing .meta files, missing "
                "scripts) before Unity imports it, without an Editor",
                add_args=_add_args, run=_run, engines=("unity",))
