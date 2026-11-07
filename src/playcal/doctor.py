"""playcal doctor: say what's missing before a loop run fails halfway. Checks Python, git, the Unity
CLI, which Editors it can see (and whether one is stuck on a dialog or in Safe Mode), and whether a
project has the playcal SDK.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .loop import Runner, _norm, _path, unity_runner
from .registry import Module


def diagnose(runner: Runner, project: str | None = None) -> list[dict]:
    out = []
    add = lambda name, status, detail: out.append({"check": name, "status": status, "detail": detail})
    v = sys.version_info
    add("python", "ok" if v >= (3, 10) else "fail", f"{v.major}.{v.minor}" + ("" if v >= (3, 10) else ", need 3.10+"))
    add("git", "ok" if shutil.which("git") else "warn",
        "found" if shutil.which("git") else "not on PATH; checkpoint and tests affected need it")
    code, env = runner(["pipeline", "list"])
    errs = env.get("errors") or []
    if code == 127 or any(e.get("code") == "UNITY_CLI_MISSING" for e in errs):
        add("unity cli", "fail", "`unity` not on PATH; install the Unity CLI or set PLAYCAL_UNITY to its path")
        return out
    if not env.get("success", code == 0):
        add("unity cli", "fail", "; ".join(e.get("message", "") for e in errs) or f"pipeline list exited {code}")
        return out
    add("unity cli", "ok", _cli_version() or "answers")
    insts = (env.get("data") or {}).get("instances") or []
    if project:
        insts = [i for i in insts if _path(i) and _norm(_path(i)) == _norm(project)]
    if not insts:
        add("editor", "warn", "no open Editor" + (f" for {project}" if project else "") + "; loop, query and snap need one")
    for i in insts:
        name = f"editor {_path(i) or '?'}"
        if (i.get("safeMode") or {}).get("detected"):
            add(name, "fail", "in Safe Mode: fix the compile errors or leave Safe Mode in the Editor")
        elif i.get("hasPipelinePackage") is False:
            add(name, "fail", "the Unity CLI's pipeline package isn't in this project")
        elif i.get("isRunning") and (i.get("pipelineServer") or {}).get("isReachable") is False:
            add(name, "fail", "running but not answering; usually a modal dialog is open in the Editor")
        elif i.get("isRunning"):
            add(name, "ok", "running and reachable")
        else:
            add(name, "warn", "known but not running")
    if project:
        manifest = Path(project) / "Packages" / "manifest.json"
        try:
            has = "com.playcal.sdk" in json.loads(manifest.read_text(encoding="utf-8")).get("dependencies", {})
            add("sdk", "ok" if has else "warn", "com.playcal.sdk installed" if has else
                "com.playcal.sdk not in Packages/manifest.json (only needed for events and frame samples)")
        except (OSError, json.JSONDecodeError) as e:
            add("sdk", "warn", f"can't read {manifest}: {e}")
    return out


def _cli_version() -> str | None:
    """`unity --version` prints plain text, not the JSON envelope, so it doesn't go through the runner."""
    try:
        p = subprocess.run([os.environ.get("PLAYCAL_UNITY", "unity"), "--version"], capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip().splitlines()[0] if p.returncode == 0 and p.stdout.strip() else None


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", help="Unity project folder to check (default: whatever Editors are open)")
    p.add_argument("--json", action="store_true", help="print the checks as JSON")


def _run(args: argparse.Namespace) -> int:
    checks = diagnose(unity_runner, args.project)
    if args.json:
        print(json.dumps(checks, ensure_ascii=False))
    else:
        for c in checks:
            print(f"{c['status']:4}  {c['check']}: {c['detail']}")
        print("\nSomething wrong or missing? github.com/RizgarOzan/playcal/issues")
    return 1 if any(c["status"] == "fail" for c in checks) else 0


MODULE = Module(name="doctor", summary="check Python, git, the Unity CLI, open Editors and the SDK before a run",
                add_args=_add_args, run=_run)
