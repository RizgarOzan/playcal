"""Run history: loop, observe, replay and visual each add one JSON line to .playcal/history.jsonl
(relative to where playcal runs). `playcal ui` draws it. Set PLAYCAL_HISTORY to another file, or to
"off" to write nothing. A history that can't be written never fails the command."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT = ".playcal/history.jsonl"


def path() -> Path | None:
    value = os.environ.get("PLAYCAL_HISTORY", DEFAULT)
    return None if value.lower() in ("", "0", "off") else Path(value)


def record(tool: str, action: str, out: dict, code: int, cmd: str = "") -> None:
    p = path()
    if p is None:
        return
    entry = {"t": round(time.time(), 3), "tool": tool, "action": action, "cmd": cmd, "exit": code, "out": out}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def read(p: Path) -> list[dict]:
    """Every readable line; a torn or hand-edited line is skipped, not fatal."""
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and "tool" in e:
            entries.append(e)
    return entries
