"""playcal loop: run one Unity Editor command the way an agent should, then report one JSON digest.

  settle  wait until the Editor is really ready: "ready" on several polls in a row,
          so a compile or domain reload that flickers between polls isn't missed.
          While Unity compiles or reloads, `unity status` lists no instance at all, so busy and
          gone look the same; Safe Mode and an Editor stuck on a dialog are told apart through
          `unity pipeline list`. --compile also runs `unity recompile`, because an Editor with
          compile errors still reports "ready".
  run     ready check -> unity command NAME ... -> ready check again -> verify.
          --verify "CMD ARGS" re-reads the Editor after the write, --expect PATH=VALUE checks
          the read (or the command's own result when there is no --verify), --diff shows only
          what changed in the --verify read between before and after. When the command says
          it needs a recompile (package resolve does), the settle after it first waits for the
          reload to start, so a reload that begins a few seconds later isn't missed.
          Each ready check is one `editor status` call, with the --diff snapshot or the --verify
          read in the same `job batch`. It counts only when the Editor itself says it's ready, not
          compiling and not reloading; anything else (busy, no answer, a write that starts a
          reload, --compile) goes through settle's `unity status` polls instead. Every `unity`
          call costs about 3 s, so a quiet write is 3 calls instead of the 6 it started at.
          A refused write gets the same `editor status` check without the read; if the Editor
          doesn't answer it, settle runs and says why.

Every digest says whose fault a failure is: "infra" (no Editor, Safe Mode, stuck, CLI broken)
or "task" (the command failed, or it said it worked but the check disagrees).
Built on Unity's official CLI (`unity status`, `unity pipeline list`, `unity command`).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from typing import Callable, Sequence

from . import checks, history
from .registry import Module

Runner = Callable[[list[str]], "tuple[int, dict]"]

EXIT = {"ready": 0, "command_failed": 1, "ambiguous": 2, "timeout": 3, "no_editor": 3, "safe_mode": 4,
        "verify_failed": 5, "editor_blocked": 4, "compile_error": 6, "compile_check_failed": 3}
INFRA = {"ambiguous", "timeout", "no_editor", "safe_mode", "editor_blocked", "compile_check_failed"}


def unity_runner(args: list[str], timeout: float = 120) -> tuple[int, dict]:
    """Calls the real `unity` binary (or $PLAYCAL_UNITY) and parses its JSON envelope from stdout."""
    exe = os.environ.get("PLAYCAL_UNITY", "unity")
    env = dict(os.environ, UNITY_NO_UPDATE_CHECK="1", UNITY_NO_CONSENT_PROMPT="1")
    try:
        p = subprocess.run([exe, *args, "--json", "--no-banner", "--non-interactive"], capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout, env=env)
    except FileNotFoundError:
        return 127, {"success": False, "errors": [{"code": "UNITY_CLI_MISSING", "message": f"{exe} not on PATH"}]}
    except subprocess.TimeoutExpired:
        return 124, {"success": False, "errors": [{"code": "UNITY_CLI_TIMEOUT", "message": f"{args[0]} took over {timeout}s"}]}
    try:
        return p.returncode, json.loads(p.stdout)
    except json.JSONDecodeError:
        return p.returncode, {"success": False, "errors": [{"code": "UNITY_CLI_BAD_JSON", "message": p.stdout[-500:] or p.stderr[-500:]}]}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _path(inst: dict) -> str | None:
    return inst.get("projectPath") or inst.get("project")  # pipeline list says projectPath, status says project


def _pick(instances: list[dict], project: str | None) -> tuple[dict | None, str | None]:
    if project:
        hits = [i for i in instances if _path(i) and _norm(_path(i)) == _norm(project)]
        return (hits[0], None) if hits else (None, None)
    if len(instances) > 1:
        return None, "ambiguous"
    return (instances[0] if instances else None), None


def _unseen(runner: Runner, project: str | None) -> str | None:
    """Why `status` can't see an Editor: "safe_mode", "editor_blocked" (the process runs but its
    server doesn't answer, usually a modal dialog such as the Safe Mode prompt), or None."""
    _, env = runner(["pipeline", "list"])
    for inst in (env.get("data") or {}).get("instances") or []:
        path = _path(inst)
        if (project and path and _norm(path) != _norm(project)) or inst.get("hasPipelinePackage") is False:
            continue
        if (inst.get("safeMode") or {}).get("detected"):
            return "safe_mode"
        if inst.get("isRunning") and (inst.get("pipelineServer") or {}).get("isReachable") is False:
            return "editor_blocked"
    return None


def settle(runner: Runner, project: str | None = None, timeout: float = 120, poll: float = 0.5, stable: int = 2,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           await_gone: float = 0) -> dict:
    """await_gone: for up to this many seconds, don't count "ready" until the Editor has gone away
    once (a reload that the last command started but that hasn't begun yet)."""
    start, streak, seen, state, checked_safe = clock(), 0, [], None, False
    while True:
        _, env = runner(["status"])
        inst, problem = _pick((env.get("data") or {}).get("instances") or [], project)
        if problem:
            return _settled(problem, project, None, seen, clock() - start)
        state = inst.get("state") if inst else None
        if not seen or seen[-1] != (state or "none"):
            seen.append(state or "none")
        early = await_gone and "none" not in seen and clock() - start < await_gone
        if inst and state == "ready" and not early:
            streak += 1
            if streak >= stable:
                return _settled("ready", project or inst.get("project"), state, seen, clock() - start)
        else:
            streak = 0
            if inst is None and not checked_safe:
                checked_safe = True
                if _unseen(runner, project) == "safe_mode":
                    return _settled("safe_mode", project, None, seen, clock() - start)
        if clock() - start >= timeout:
            reason = "timeout" if inst else _unseen(runner, project) or "no_editor"
            return _settled(reason, project, state, seen, clock() - start)
        sleep(poll)


def _settled(reason: str, project, state, seen, waited: float) -> dict:
    hints = {
        "safe_mode": "Editor is in Safe Mode because of compile errors. Fix them in the C# source, then restart Unity.",
        "no_editor": "No Editor with the Pipeline package found. Open the project in Unity, or run `unity pipeline install`.",
        "ambiguous": "More than one Editor is open. Pass --project-path.",
        "timeout": "Editor never stayed ready. It may be compiling, importing, or blocked by a modal dialog.",
        "editor_blocked": "Unity is running but not answering, usually a dialog such as the Safe Mode prompt after "
                          "compile errors. Run `playcal logs` for the first error; a person has to answer the dialog.",
    }
    out = {"settled": reason == "ready", "reason": reason, "project": project, "state": state,
           "states_seen": seen, "waited_ms": round(waited * 1000)}
    if reason in hints:
        out["hint"] = hints[reason]
    return out


def _call(runner: Runner, argv: list[str], project: str | None) -> dict:
    rc, env = runner(["command", *argv, *(["--project-path", project] if project else [])])
    return {"success": bool(env.get("success")) and rc == 0, "exit_code": rc,
            "errors": env.get("errors") or [], "warnings": env.get("warnings") or [], "data": env.get("data")}


def _op(argv: Sequence[str]) -> dict | None:
    """`scene get_hierarchy --path X` as a batch op: {"command": "scene get_hierarchy", "params": {"path": "X"}}.
    None when the words don't fit that shape (a stray positional argument)."""
    words = list(argv)
    i = next((k for k, w in enumerate(words) if w.startswith("--")), len(words))
    params, rest = {}, words[i:]
    while rest:
        flag = rest.pop(0)
        if not flag.startswith("--"):
            return None
        key, eq, val = flag[2:].partition("=")
        params[key] = val if eq else rest.pop(0) if rest and not rest[0].startswith("--") else True
    return {"command": " ".join(words[:i]), "params": params} if i else None


def _reads(runner, project, reads: Sequence[Sequence[str]]) -> list[dict]:
    """Several Editor reads in one `job batch` call; each result looks like loop._call's. A read that
    fails inside the batch (not batchable, or an older Pipeline without `job batch`) runs again on
    its own, so its error is the one the plain command gives."""
    ops = [_op(r) for r in reads]
    out: list = [None] * len(reads)
    if sum(o is not None for o in ops) > 1:
        res = _call(runner, ["job", "batch", "--operations", json.dumps([o for o in ops if o]),
                                  "--on-error", "continue"], project)
        items = iter((((res["data"] or {}).get("result") or {}).get("results") or []) if res["success"] else [])
        for k, o in enumerate(ops):
            item = next(items, None) if o else None
            if o and item and item.get("success"):
                out[k] = {"success": True, "exit_code": 0, "errors": [], "warnings": [],
                          "data": {"command": o["command"], "parameters": o["params"], "result": item.get("result")}}
    return [r or _call(runner, list(argv), project) for r, argv in zip(out, reads)]


def compile_check(runner: Runner, project: str | None, timeout: float = 120, **settle_kw) -> dict:
    """`unity recompile`, trimmed to what an agent needs. A recompile sent while Unity's own
    auto-refresh is reloading fails (400, timeout, no instance), so settle and try once more."""
    args = ["recompile", *(["--project-path", project] if project else []), "--timeout", str(int(min(timeout, 110)))]
    env: dict = {}
    for attempt in range(2):
        rc, env = runner(args)
        data = env.get("data") or {}
        if data.get("compilationFailed") or data.get("failed"):
            errs = [{"file": str(e.get("file", "")).replace("\\", "/"), "line": e.get("line"), "code": e.get("code"),
                     "message": e.get("message")} for e in data.get("errors") or []]
            return {"ok": False, "count": len(errs), "errors": errs[:20]}
        if env.get("success") and rc == 0:
            return {"ok": True, "status": data.get("status"), "warnings": len(data.get("warnings") or [])}
        if attempt == 0:
            settle(runner, project, timeout, **settle_kw)
    return {"ok": None, "errors": env.get("errors") or []}


def _wants_reload(node) -> bool:
    if isinstance(node, dict):
        return node.get("requiresRecompile") is True or any(_wants_reload(v) for v in node.values())
    return isinstance(node, list) and any(_wants_reload(v) for v in node)


def _compile_step(digest: dict, runner: Runner, project, timeout, settle_kw) -> str | None:
    """Runs the compile check into the digest; returns a failure reason or None."""
    digest["compile"] = c = compile_check(runner, project, timeout, **settle_kw)
    if c["ok"] is None:
        return "compile_check_failed"
    if not c["ok"]:
        return "compile_error"
    if c.get("status") == "completed":  # scripts were rebuilt, so a domain reload follows
        again = settle(runner, project, timeout, await_gone=10, **settle_kw)
        if not again["settled"]:
            return again["reason"]
    return None


def _fault(reason: str, result: dict | None) -> str | None:
    if reason == "ready":
        return None
    if reason in INFRA:
        return "infra"
    codes = [str(e.get("code", "")) for e in (result or {}).get("errors", [])]
    return "infra" if any(c.startswith("UNITY_CLI_") for c in codes) else "task"


def _save_checkpoint(repo: str, message: str) -> dict:
    """Save files before the write. A broken checkpoint is reported, not fatal: the command still runs."""
    from . import checkpoint as cp
    try:
        return cp.save(cp.Repo(repo), message)
    except cp.GitError as e:
        return {"error": str(e)}


def _state(data):
    """What the Editor returned, without the request echo (command, parameters, target): a batched
    read echoes them differently from a plain one, and --diff shouldn't report that."""
    return {"result": data["result"]} if isinstance(data, dict) and "result" in data else data


def _idle_read(runner: Runner, project: str | None, verify: list[str] | None) -> tuple[dict, dict | None] | None:
    """One `editor status` call, with a read in the same batch when one is wanted, instead of two
    `unity status` polls and a separate read. Used before the write (the read is the --diff snapshot)
    and after a write that starts no reload (the read is --verify). Counts only when the Editor itself
    says it is ready, not compiling and not reloading; None sends the caller back to settle."""
    t0 = time.monotonic()
    res = _reads(runner, project, [["editor", "status"], *([verify] if verify else [])])
    st = ((res[0]["data"] or {}).get("result") or {}) if res[0]["success"] else {}
    if st.get("status") != "ready" or st.get("compiling") is not False or st.get("domainReloadInProgress") is not False:
        return None
    after = {"settled": True, "reason": "ready", "project": project or st.get("projectPath"), "state": "ready",
             "states_seen": ["ready"], "waited_ms": round((time.monotonic() - t0) * 1000), "via": "editor status",
             "code_load_count": st.get("codeLoadCount")}
    return after, (res[1] if verify else None)


def run_command(runner: Runner, name: str, params: list[str], project: str | None = None, timeout: float = 120,
                verify: list[str] | None = None, expect: list[str] = (), want_diff: bool = False,
                checkpoint: str | None = None, compile: bool = False, reload_wait: float | None = None,
                **settle_kw) -> dict:
    t0 = time.monotonic()
    digest: dict = {"command": name, "params": params}
    quick = _idle_read(runner, project, verify if verify and want_diff else None)
    if quick:
        digest["before"], snapshot = quick
    else:
        digest["before"], snapshot = settle(runner, project, timeout, **settle_kw), None
    if not digest["before"]["settled"]:
        digest["ok"], digest["reason"] = False, digest["before"]["reason"]
        digest["fault"] = _fault(digest["reason"], None)
        return digest
    if checkpoint is not None:
        digest["checkpoint"] = _save_checkpoint(checkpoint, f"before {name} {' '.join(params)}".strip())
    if snapshot is None and verify and want_diff:
        snapshot = _call(runner, verify, project)
    digest["result"] = _call(runner, [name, *params], project)
    wait = reload_wait if reload_wait is not None else 20 if _wants_reload(digest["result"]["data"]) else 0
    ok = digest["result"]["success"]
    quick = _idle_read(runner, project, verify if ok else None) if not wait and not compile else None
    if quick:
        digest["after"], read = quick
    else:
        digest["after"] = settle(runner, project, timeout, await_gone=wait, **settle_kw)
        read = None
    if wait:
        digest["after"]["reload_seen"] = "none" in digest["after"]["states_seen"]
    reason = ("command_failed" if not digest["result"]["success"] else
              digest["after"]["reason"] if not digest["after"]["settled"] else "ready")
    if reason == "ready" and compile:
        reason = _compile_step(digest, runner, project, timeout, settle_kw) or "ready"

    if reason == "ready" and (verify or expect):
        read = read or (_call(runner, verify, project) if verify else digest["result"])
        if verify:
            digest["verify"] = {"command": verify, "success": read["success"], "errors": read["errors"]}
        digest["checks"] = [checks.check(read["data"], e) for e in expect]
        if snapshot is not None and snapshot["success"] and read["success"]:
            digest["diff"] = checks.diff(_state(snapshot["data"]), _state(read["data"]))
        if not read["success"]:
            reason, digest["fault"] = "verify_failed", _fault("command_failed", read)
        elif not all(c["ok"] for c in digest["checks"]):
            reason = "verify_failed"

    digest["ok"], digest["reason"] = reason == "ready", reason
    digest.setdefault("fault", _fault(reason, digest["result"]))
    if not digest["ok"] and "id" in digest.get("checkpoint", {}):
        digest["undo"] = f"playcal checkpoint restore {digest['checkpoint']['id']}"
    digest["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return digest


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["settle", "run"], help="settle: wait for a ready Editor. run: settle, run, settle")
    p.add_argument("name", nargs="?", help="Editor command for `run` (see `unity command`)")
    p.add_argument("params", nargs="*", help="passed to the command unchanged; put them after --, e.g. run save_scene -- --name x")
    p.add_argument("--project-path", help="Unity project folder; needed when several Editors are open")
    p.add_argument("--timeout", type=float, default=120, help="seconds to wait for a ready Editor (default 120)")
    p.add_argument("--verify", metavar="CMD", help='read command run after the write, e.g. "get_hierarchy --depth 1"')
    p.add_argument("--expect", action="append", default=[], metavar="PATH=VALUE",
                   help="check on the --verify read (or the command result). PATH=VALUE, PATH!=VALUE, PATH~TEXT, "
                        "or bare PATH (exists). * matches any list item: objects.*.name=Probe. Repeatable")
    p.add_argument("--diff", action="store_true", help="run --verify before the write too and report only what changed")
    p.add_argument("--checkpoint", nargs="?", const=".", metavar="REPO",
                   help="save files with playcal checkpoint before the write; on failure the digest says how to undo")
    p.add_argument("--compile", action="store_true",
                   help="also run `unity recompile` and fail with the compile errors; the Editor says ready even when scripts don't build")
    p.add_argument("--reload-wait", type=float, metavar="SECONDS",
                   help="after the write, wait this long for a reload to start before trusting ready "
                        "(default: 20 when the command says it needs a recompile, else 0)")
    p.add_argument("--json", action="store_true", help="print the digest as JSON")


def _run(args: argparse.Namespace) -> int:
    if args.action == "run" and not args.name:
        print("playcal loop run: give an Editor command name", file=sys.stderr)
        return 2
    if args.action == "settle":
        out = settle(unity_runner, args.project_path, args.timeout)
        reason = out["reason"]
        if reason == "ready" and args.compile:
            reason = _compile_step(out, unity_runner, args.project_path, args.timeout, {}) or "ready"
            out["reason"], out["settled"] = reason, reason == "ready"
    else:
        verify = shlex.split(args.verify) if args.verify else None
        out = run_command(unity_runner, args.name, args.params, args.project_path, args.timeout,
                          verify=verify, expect=args.expect, want_diff=args.diff,
                          checkpoint=args.checkpoint, compile=args.compile, reload_wait=args.reload_wait)
        reason = out["reason"]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(f"{reason} ({' -> '.join(out.get('states_seen') or out.get('before', {}).get('states_seen', []))})")
        if "hint" in out:
            print(out["hint"])
        for e in (out.get("result") or {}).get("errors", []):
            print(f"  {e.get('code')}: {e.get('message')}")
        for e in (out.get("compile") or {}).get("errors", []):
            print(f"  {e.get('file')}:{e.get('line')} {e.get('code')}: {e.get('message')}")
        for c in out.get("checks", []):
            print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['expect']}  (got {json.dumps(c['actual'], ensure_ascii=False)})")
        for d in out.get("diff", []):
            print(f"  diff {json.dumps(d, ensure_ascii=False)}")
        if out.get("fault"):
            print(f"  fault: {out['fault']}")
        if out.get("undo"):
            print(f"  undo: {out['undo']}")
    code = EXIT.get(reason, 1)
    history.record("loop", args.action, out, code, " ".join([args.name or "", *args.params]).strip())
    return code


MODULE = Module(name="loop", summary="run a Unity Editor command safely: wait until ready, run, wait again, one digest",
                add_args=_add_args, run=_run, engines=("unity",))
