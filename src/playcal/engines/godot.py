"""Godot through `godot --headless` and a probe script (godot_probe.gd), nothing installed in the project.

Godot has no live editor server to ask, so each verb is one headless run:
  check    load every .gd so parse errors print, the Godot version of "did it compile"
  tree     run the main scene for one frame and read its node tree (for snap)
  observe  run the main scene --seconds, read the tree after the first frame and at the end
Errors come from what Godot prints: `ERROR:` / `SCRIPT ERROR:` lines, with the res:// file:line
from the lines under them. Node paths are the identity in a tree, so a restart changes nothing.
Set PLAYCAL_GODOT to the Godot binary when `godot` isn't on PATH.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

PROBE = Path(__file__).with_name("godot_probe.gd")
# (exit code, everything Godot printed); tests pass a fake
Runner = Callable[[list[str], float], "tuple[int, str]"]

ERROR = re.compile(r"^\s*(?P<kind>SCRIPT ERROR|USER ERROR|ERROR): (?P<msg>.*)$")
WHERE = re.compile(r"\((?P<file>res://[^:)]+):(?P<line>\d+)\)|(?P<file2>res://[^\s:]+):(?P<line2>\d+)")


def godot_runner(args: list[str], timeout: float = 120) -> tuple[int, str]:
    exe = os.environ.get("PLAYCAL_GODOT", "godot")
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except FileNotFoundError:
        return 127, f"ERROR: GODOT_MISSING: {exe} not found; set PLAYCAL_GODOT to the Godot binary"
    except subprocess.TimeoutExpired:
        return 124, f"ERROR: GODOT_TIMEOUT: Godot ran over {timeout}s"
    return p.returncode, p.stdout + "\n" + p.stderr


def summarize(text: str, limit: int = 20) -> dict:
    """Godot's printed errors, the same shape as logs.summarize: repeats collapsed, first cause first."""
    lines = text.splitlines()
    found: dict[tuple, dict] = {}
    for n, raw in enumerate(lines):
        m = ERROR.match(raw)
        if not m:
            continue
        msg = m["msg"].strip()
        where: dict = {}
        for nxt in lines[n + 1:n + 8]:
            if ERROR.match(nxt):
                break
            w = WHERE.search(nxt)
            if w:
                where = {"file": (w["file"] or w["file2"]).removeprefix("res://"), "line": int(w["line"] or w["line2"])}
                break
        if not where:  # a parse error names its own file in the message
            w = WHERE.search(msg)
            if w:
                where = {"file": (w["file"] or w["file2"]).removeprefix("res://"), "line": int(w["line"] or w["line2"])}
        code = ("ParseError" if "Parse Error" in msg else "ScriptError" if m["kind"] == "SCRIPT ERROR"
                else msg.split(":", 1)[0] if re.match(r"^[A-Z_]+:", msg) else "ERROR")
        key = (code, msg, where.get("file"), where.get("line"))
        if key in found:
            found[key]["count"] += 1
        else:
            found[key] = {"kind": "compile" if code == "ParseError" else "error", "code": code,
                          "message": msg[:300], **where, "count": 1, "first_line": n + 1}
    entries = list(found.values())
    parse = [e for e in entries if e["kind"] == "compile"]
    out = {"ok": not entries, "first_cause": (parse or entries or [None])[0], "errors": entries[:limit],
           "error_kinds": len(entries), "error_total": sum(e["count"] for e in entries)}
    if len(entries) > limit:
        out["truncated"] = len(entries) - limit
    return out


class Godot:
    name = "godot"

    def __init__(self, runner: Runner | None = None):
        self.runner = runner or godot_runner

    def _probe(self, project: str | None, mode: str, timeout: float, **extra) -> tuple[int, str, dict]:
        project = project or "."
        if not (Path(project) / "project.godot").exists():
            return 2, "", {"error": f"no project.godot in {Path(project).resolve()}"}
        if not (Path(project) / ".godot").exists():  # a fresh checkout: import once, or res:// loads fail
            self.runner(["--headless", "--path", project, "--import"], timeout)
        fd, out = tempfile.mkstemp(suffix=".json", prefix="playcal-godot-")
        os.close(fd)
        try:
            user = [f"--mode={mode}", f"--out={Path(out).as_posix()}", *(f"--{k}={v}" for k, v in extra.items() if v)]
            rc, text = self.runner(["--headless", "--path", project, "-s", str(PROBE), "--", *user], timeout)
            try:
                data = json.loads(Path(out).read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                data = {}
        finally:
            Path(out).unlink(missing_ok=True)
        return rc, text, data

    def check(self, project: str | None, timeout: float = 120) -> dict:
        """loop settle for Godot: "ready" when every script loads, "compile_error" with file:line when not."""
        rc, text, data = self._probe(project, "check", timeout)
        con = summarize(text)
        if "checked" not in data:
            reason = "no_editor" if rc in (127, 2) else "timeout" if rc == 124 else "compile_check_failed"
            hint = data.get("error") or (con["first_cause"] or {}).get("message")
            return {"settled": False, "reason": reason, "engine": "godot", "console": con,
                    **({"hint": hint} if hint else {})}
        parse = [e for e in con["errors"] if e["kind"] == "compile"]
        return {"settled": not parse, "reason": "ready" if not parse else "compile_error", "engine": "godot",
                "scripts": data["checked"], "compile": {"ok": not parse, "count": len(parse), "errors": parse}}

    def tree(self, project: str | None, scene: str | None = None, timeout: float = 120) -> dict:
        rc, text, data = self._probe(project, "tree", timeout, scene=scene)
        if "tree" not in data:
            raise RuntimeError(data.get("error") or (summarize(text)["first_cause"] or {}).get("message")
                               or f"godot exited {rc} without a tree")
        return data

    def observe(self, project: str | None, seconds: float = 3, scene: str | None = None,
                timeout: float = 120) -> tuple[int, dict, dict]:
        """(exit code, probe output, error summary); the digest is built by playcal observe."""
        rc, text, data = self._probe(project, "observe", timeout + seconds, scene=scene, seconds=seconds)
        return rc, data, summarize(text)
