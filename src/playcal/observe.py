"""playcal observe: press Play, let the game run a few seconds, and report one digest of what changed
in the scene and what went wrong. A screenshot is taken only when something went red.

  1. settle, then one batch: read the scene (default `scene get_hierarchy`), mark the console
     cursor, ask whether Play Mode is already on
  2. enter Play Mode and wait for `editor status` to say it's playing
  3. let the game run --seconds, then one batch: read the scene again, pull the console errors
     logged since the mark
  4. --expect checks run on the second read
  5. red (errors, a failed check, a failed read) -> `capture game_view` into --shots
  6. leave Play Mode, diff the two reads with snap's rules (instanceIds left out)

Every `unity` call costs about 3 s, so the reads go through `job batch`, several to one call; a read
the batch won't take runs on its own. `played_ms` says how long the game ran before the second
read: at least `min`, at most `max`.

The scene diff is reported, not gated on: a running game is supposed to change. Gate on it with
--expect. If the Editor is already in Play Mode, observe watches it for --seconds and leaves it running.
"""
from __future__ import annotations

import argparse
import json
import shlex
import time
from typing import Callable, Sequence

from . import checks, history, logs, loop, snap
from . import engines
from .registry import Module

EXIT = {"clean": 0, "errors": 1, "expect_failed": 1, "read_failed": 1, "play_failed": 1, "console_unread": 3}
NOT_PLAYING = {"code": "NOT_PLAYING", "message": "the Editor didn't enter Play Mode (compile errors block it)"}


def _mark(res: dict) -> dict | None:
    r = ((res["data"] or {}).get("result") or {}) if res["success"] else {}
    return {"cursor": r["cursor"], "session": r.get("session")} if "cursor" in r else None


def _console(res: dict | None, limit: int) -> dict:
    if res is None:
        return {"read": False, "errors_seen": None}
    if not res["success"]:
        return {"read": False, "errors_seen": None, "problem": res["errors"]}
    r = (res["data"] or {}).get("result") or {}
    entries = r.get("entries") or []
    lines = [ln for e in entries for ln in [str(e.get("message", "")), *str(e.get("stackTrace") or "").splitlines()]]
    out = {"read": True, "errors_seen": len(entries), **logs.summarize(lines, limit)}
    out.pop("lines_read", None)
    for flag in ("dropped", "reset"):  # buffer overflowed, or the console was cleared mid-run
        if r.get(flag):
            out[flag] = True
    return out


def _console_verdict(con: dict) -> str | None:
    """"errors" if errors were read; "console_unread" if some could have been missed; else None."""
    if con.get("errors_seen"):
        return "errors"
    if not con.get("read") or con.get("reset") or con.get("dropped"):
        return "console_unread"
    return None


def _playing(res: dict) -> bool:
    """An `editor status` result says Play Mode, running or paused."""
    return res["success"] and ((res["data"] or {}).get("result") or {}).get("playMode") in ("playing", "paused")


def _enter_play(runner, project, timeout, sleep, clock) -> dict:
    """Wait for Play Mode after `editor.playmode start`. Asks `editor status`, not `unity status`:
    start comes back once the reload is done and `editor status` says "playing" right away, while
    `unity status` can list no Editor for a few seconds more. An Editor that answers and sits idle in
    Edit Mode isn't going to enter it (compile errors block Play), so that ends the wait."""
    eng, start = engines.wrap(runner), clock()
    while True:
        res = eng.status(project)
        r = ((res["data"] or {}).get("result") or {}) if res["success"] else {}
        busy = not res["success"] or bool(r.get("compiling") or r.get("domainReloadInProgress"))
        if _playing(res) or not busy or clock() - start >= timeout:
            return {"playing": _playing(res), "busy": busy and not _playing(res),
                    "waited_ms": round((clock() - start) * 1000)}
        sleep(0.5)


def observe(runner, project: str | None = None, read: Sequence[str] = ("scene", "get_hierarchy"), seconds: float = 3,
            play: bool = True, expect: Sequence[str] = (), shots: str | None = ".playcal/observe", name: str = "observe",
            timeout: float = 120, ignore=snap.VOLATILE, limit: int = 50,
            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
    t0, eng = clock(), engines.wrap(runner)
    d: dict = {"read": list(read), "seconds": seconds,
               "before": eng.ready(project, timeout, sleep=sleep, clock=clock)}

    def done(reason: str, result=None) -> dict:
        d["ok"], d["reason"] = reason == "clean", reason
        # our own reasons are the game's fault unless the CLI itself broke; settle's keep loop's verdict
        d["fault"] = (None if reason == "clean" else "infra" if reason == "console_unread" else
                      loop._fault("command_failed" if reason in EXIT else reason, result))
        d["elapsed_ms"] = round((clock() - t0) * 1000)
        return d

    if not d["before"]["settled"]:
        return done(d["before"]["reason"])
    t_ask = clock()
    first, con_mark, status = eng.read([list(read), eng.CONSOLE_MARK, eng.STATUS], project)
    t_watch = clock()
    if not first["success"]:
        d["read_errors"] = first["errors"]
        return done("read_failed", first)
    mark = _mark(con_mark)
    since = [eng.console_since(mark)] if mark else []

    started = False
    try:  # whatever happens after Play starts, leave Play Mode again
        if play and not _playing(status):
            t_ask = clock()
            res = eng.play(project)
            if not res["success"]:
                d["play_errors"] = res["errors"]
                return done("play_failed", res)
            started = True
            d["entered_play"] = _enter_play(runner, project, timeout, sleep, clock)
            t_watch = clock()
            if not d["entered_play"]["playing"]:
                d["console"] = _console(eng.read(since, project)[0] if mark else None, 20)
                if d["entered_play"]["busy"]:
                    return done("timeout")
                d["play_errors"] = [NOT_PLAYING]
                return done("play_failed")
        sleep(max(0.0, seconds - (clock() - t_watch)))

        t_read = clock()
        second, *con = eng.read([list(read), *since], project)
        d["played_ms"] = {"min": round((t_read - t_watch) * 1000), "max": round((clock() - t_ask) * 1000)}
        d["console"] = _console(con[0] if con else None, 20)
        d["checks"] = [checks.check(second["data"], e) for e in expect] if second["success"] else []
        heard = _console_verdict(d["console"])
        reason = ("read_failed" if not second["success"] else "errors" if heard == "errors"
                  else "expect_failed" if not all(c["ok"] for c in d["checks"]) else heard or "clean")
        if not second["success"]:
            d["read_errors"] = second["errors"]
        if reason != "clean" and shots:
            cap = eng.screenshot(f"{shots.rstrip('/')}/{name}.png", project)
            res = (cap["data"] or {}).get("result") or {}
            d["shot"] = res.get("savedPath") if cap["success"] else {"failed": cap["errors"]}
    finally:
        if started:  # stop comes back once Edit Mode is back; the next command settles first anyway
            d["left_play"] = eng.stop(project)["success"]
            if "elapsed_ms" in d:  # an early return inside the try: count the stop too
                d["elapsed_ms"] = round((clock() - t0) * 1000)
    if second["success"]:
        d["diff"] = snap.compare(first["data"], second["data"], ignore, limit)
    return done(reason, second)


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--engine", choices=("unity", "godot"), default="unity",
                   help="unity talks to the open Editor; godot runs the main scene headless (default unity)")
    p.add_argument("--scene", help="godot only: scene to run instead of the main scene (res://...)")
    p.add_argument("--read", default="scene get_hierarchy", metavar="CMD",
                   help='Editor read to diff before and during play (default "scene get_hierarchy")')
    p.add_argument("--seconds", type=float, default=3, help="how long to let the game run before the second read (default 3)")
    p.add_argument("--no-play", action="store_true", help="don't touch Play Mode; diff two reads --seconds apart")
    p.add_argument("--expect", action="append", default=[], metavar="PATH=VALUE",
                   help="check on the read during play, same syntax as playcal loop --expect. Repeatable")
    p.add_argument("--shots", default=".playcal/observe", metavar="DIR",
                   help="where the red-run screenshot goes, relative to the Unity project (default .playcal/observe)")
    p.add_argument("--name", default="observe", help="screenshot file name without .png (default observe)")
    p.add_argument("--no-shot", action="store_true", help="never take a screenshot")
    p.add_argument("--ignore", action="append", default=None, metavar="KEY",
                   help="field to leave out of the diff, repeatable (default: instanceId, isDirty)")
    p.add_argument("--limit", type=int, default=50, help="most diff entries to list (default 50)")
    p.add_argument("--project-path", help="Unity project folder (needed when several Editors are open), or the Godot project folder")
    p.add_argument("--timeout", type=float, default=120, help="seconds to wait for a ready Editor (default 120)")
    p.add_argument("--json", action="store_true", help="print the digest as JSON")


def observe_godot(godot, project: str | None = None, seconds: float = 3, expect: Sequence[str] = (),
                  scene: str | None = None, timeout: float = 120, ignore=snap.VOLATILE, limit: int = 50) -> dict:
    """The Godot run: one headless run of the main scene. No Play Mode to enter and no screenshot."""
    rc, data, con = godot.observe(project, seconds, scene, timeout)
    d: dict = {"engine": "godot", "seconds": seconds, "scene": data.get("scene")}
    con = {"read": True, "errors_seen": con["error_total"], **con}
    d["console"] = con
    if "after" not in data:
        d["read_errors"] = [{"code": "GODOT_RUN_FAILED", "message": data.get("error") or
                             (con.get("first_cause") or {}).get("message") or f"godot exited {rc} without a tree"}]
        reason = "read_failed"
    else:
        d["played_ms"] = {"min": data.get("played_ms", 0), "max": data.get("played_ms", 0)}
        d["checks"] = [checks.check(data["after"], e) for e in expect]
        d["diff"] = snap.compare(data["before"], data["after"], ignore, limit)
        reason = ("errors" if con["error_total"] else
                  "expect_failed" if not all(c["ok"] for c in d["checks"]) else "clean")
    d["ok"], d["reason"] = reason == "clean", reason
    d["fault"] = None if reason == "clean" else "game"
    return d


def _run(args: argparse.Namespace) -> int:
    ignore = snap.VOLATILE if args.ignore is None else [k for k in args.ignore if k]
    if args.engine == "godot":
        out = observe_godot(engines.get("godot"), args.project_path, args.seconds, args.expect, args.scene,
                            args.timeout, ignore, args.limit)
    else:
        out = observe(loop.unity_runner, args.project_path, shlex.split(args.read), args.seconds, not args.no_play,
                      args.expect, None if args.no_shot else args.shots, args.name, args.timeout, ignore, args.limit)
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        con = out.get("console") or {}
        print(f"{out['reason']}" + (f" ({out['fault']})" if out["fault"] else ""))
        for e in con.get("errors") or []:
            print(f"  {e.get('code')}: {e.get('message')}" + (f"  at {e['file']}:{e.get('line')}" if e.get("file") else "")
                  + (f"  x{e['count']}" if e.get("count", 1) > 1 else ""))
        if con.get("errors_seen") and not con.get("errors"):
            print(f"  {con['errors_seen']} console errors (none matched a known shape; see the Console)")
        for c in out.get("checks", []):
            print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['expect']}  (got {json.dumps(c['actual'], ensure_ascii=False)})")
        for e in out.get("read_errors", []) + out.get("play_errors", []):
            print(f"  {e.get('code')}: {e.get('message')}")
        if "diff" in out:
            df = out["diff"]
            print("  scene: no changes" if df["same"] else
                  f"  scene: {df['added']} added, {df['removed']} removed, {df['changed']} changed")
        if out.get("played_ms"):
            pm = out["played_ms"]
            print(f"  played: {pm['min'] / 1000:.1f}-{pm['max'] / 1000:.1f} s before the second read")
        if out.get("shot"):
            print(f"  shot: {out['shot']}")
        if "hint" in out.get("before", {}):
            print(f"  {out['before']['hint']}")
    code = EXIT.get(out["reason"], loop.EXIT.get(out["reason"], 1))
    history.record("observe", "run", out, code, args.name or "")
    return code


MODULE = Module(name="observe", summary="press Play, run the game a few seconds, diff the scene and collect console errors; screenshot only on red",
                add_args=_add_args, run=_run, engines=("unity", "godot"))
