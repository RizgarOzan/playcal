"""playcal replay: record a play session's input, play it back as a regression test, step frames.

The game reads input through the SDK (`PlaycalInput.Axis("move.x", live)`) and notes what should come
out the same with `PlaycalReplay.Mark("score", score)`. A recording is a text file of input changes
and marks per frame (`.playcal/replays/NAME.replay`, written by the SDK). A replay feeds the recorded
input back with the same Time.captureFramerate and Random seed and writes the marks it saw to
NAME.result; `check` compares the two and names the first frame where they part.

  record NAME --frames 600   press Play, record 600 frames (someone plays), leave Play
  run NAME                   press Play, replay, leave Play, compare marks; console errors count too
  run NAME --stop-at 120     replay up to frame 120 and leave the Editor paused there
  step [--frames N]          advance a paused Editor N frames, print the replay frame
  check NAME | A B           compare a recording with a result offline

The SDK picks a request up from `.playcal/replays/request` when Play starts, so nothing in the scene
has to change. Physics and anything that reads the wall clock may still drift between runs.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable

from . import history, loop, observe
from . import engines
from .registry import Module

HEADER = "playcal-replay 1"
EXIT = {"match": 0, "recorded": 0, "paused": 0, "stepped": 0, "diverged": 1, "ended_early": 1, "errors": 1,
        "bad_file": 2, "no_recording": 2, "exists": 2, "no_project": 2, "already_playing": 2, "not_playing": 2,
        "play_failed": 1, "no_sdk": 1, "no_result": 3, "eval_failed": 1, "no_marks": 1, "console_unread": 3}


class ReplayError(Exception):
    pass


def parse(text: str) -> dict:
    lines = text.replace("\r", "").split("\n")
    if not lines[0].startswith(HEADER):
        raise ReplayError(f'not a playcal replay: the first line must start with "{HEADER}"')
    head = dict(kv.split("=", 1) for kv in lines[0][len(HEADER):].split() if "=" in kv)
    out: dict = {"fps": int(head.get("fps", 60)), "seed": int(head.get("seed", 0)), "end": None, "input": [], "marks": []}
    for i, line in enumerate(lines[1:], 2):
        if not line or line.startswith("#"):
            continue
        parts = line.split(" ", 3)
        try:
            frame = int(parts[0])
            if parts[1:] == ["end"]:
                out["end"] = frame
            elif parts[1] == "in" and len(parts) == 4:
                out["input"].append((frame, parts[2], float(parts[3])))
            elif parts[1] == "mark" and len(parts) >= 3:
                out["marks"].append((frame, parts[2], parts[3] if len(parts) == 4 else ""))
            else:
                raise ValueError
        except (ValueError, IndexError):
            raise ReplayError(f"line {i}: expected '<frame> in <channel> <value>', '<frame> mark <name> <value>' "
                              f"or '<frame> end', got {line!r}") from None
    return out


def _same(a: str, b: str, tolerance: float) -> bool:
    """Same text, or two equal whole numbers, or two other numbers within the absolute tolerance."""
    if a == b:
        return True
    try:
        return int(a) == int(b)
    except ValueError:
        pass
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    return abs(x - y) <= tolerance or (math.isnan(x) and math.isnan(y))


def compare(recorded: dict, replayed: dict, tolerance: float = 1e-4) -> dict:
    """Walks both mark lists in order. The first mark that differs in frame, name or value (numbers
    within tolerance) is the divergence; a mark only one side has counts too. No marks at all proves
    nothing, so it isn't a match."""
    a, b = recorded["marks"], replayed["marks"]
    out: dict = {"marks_recorded": len(a), "marks_replayed": len(b), "end_recorded": recorded["end"],
                 "end_replayed": replayed["end"], "first_divergence": None}
    for i in range(max(len(a), len(b))):
        x, y = (a[i] if i < len(a) else None), (b[i] if i < len(b) else None)
        if x and y and x[0] == y[0] and x[1] == y[1] and _same(x[2], y[2], tolerance):
            continue
        frame = min(m[0] for m in (x, y) if m)
        out["first_divergence"] = {"frame": frame, "index": i,
                                   "expected": {"frame": x[0], "name": x[1], "value": x[2]} if x else None,
                                   "got": {"frame": y[0], "name": y[1], "value": y[2]} if y else None}
        break
    early = recorded["end"] is not None and (replayed["end"] is None or replayed["end"] < recorded["end"])
    out["reason"] = ("diverged" if out["first_divergence"] else "ended_early" if early
                     else "no_marks" if not a and not b else "match")
    return out


def _load(path: Path) -> dict:
    try:
        return parse(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ReplayError(f"can't read {path}: {e.strerror}") from None


def _project(runner, project: str | None) -> Path | None:
    """The project folder on disk: --project-path, else the one the Editor reports."""
    if project:
        p = Path(project)
    else:
        res = engines.wrap(runner).status(None)
        path = ((res["data"] or {}).get("result") or {}).get("projectPath") if res["success"] else None
        if not path:
            return None
        p = Path(path)
    return p if (p / "Assets").is_dir() else None


def _wait(paths, timeout: float, sleep, clock) -> Path | None:
    end = clock() + timeout
    while True:
        for p in paths:
            if p.exists():
                return p
        if clock() >= end:
            return None
        sleep(0.5)


def _session(runner, project: str | None, folder: Path, request: dict, wait_for: list[Path], wait: float,
             timeout: float, stay_on: Path | None, sleep, clock) -> dict:
    """Writes the request, enters Play, waits for one of `wait_for`, leaves Play unless `stay_on` showed up."""
    eng = engines.wrap(runner)
    d: dict = {"before": eng.ready(project, timeout, sleep=sleep, clock=clock), "file": None}
    if not d["before"]["settled"]:
        d["reason"] = d["before"]["reason"]
        return d
    con_mark, status = eng.read([eng.CONSOLE_MARK, eng.STATUS], project)
    if observe._playing(status):
        d["reason"] = "already_playing"  # the request is only read when Play starts
        return d
    mark = observe._mark(con_mark)
    folder.mkdir(parents=True, exist_ok=True)
    req = folder / "request"
    request = {**request, "created": int(time.time())}  # the SDK skips a request left over from an old run
    req.write_text("".join(f"{k}={v}\n" for k, v in request.items()), encoding="utf-8")
    try:  # never leave a request behind: the next Play would pick it up
        res = eng.play(project)
        if not res["success"]:
            d["reason"], d["play_errors"] = "play_failed", res["errors"]
            return d
        d["entered_play"] = observe._enter_play(runner, project, timeout, sleep, clock)
        got = None
        if d["entered_play"]["busy"]:
            d["reason"] = "timeout"
        elif not d["entered_play"]["playing"]:
            d["reason"], d["play_errors"] = "play_failed", [observe.NOT_PLAYING]
        else:
            got = _wait(wait_for, wait, sleep, clock)
            if got is None:  # a request still on disk was never read: the SDK isn't in the project
                d["reason"] = "no_sdk" if req.exists() else "no_result"
        d["console"] = observe._console(eng.run(eng.console_since(mark), project) if mark else None, 20)
        if got is None or got != stay_on:  # stop comes back once Edit Mode is back
            d["left_play"] = eng.stop(project)["success"]
        d["file"] = str(got) if got else None
        return d
    finally:
        req.unlink(missing_ok=True)


def record(runner, project: str | None, name: str, frames: int = 600, fps: int = 60, seed: int = 0,
           force: bool = False, wait: float | None = None, timeout: float = 120,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
    root = _project(runner, project)
    if root is None:
        return {"ok": False, "reason": "no_project"}
    folder = root / ".playcal" / "replays"
    out = folder / f"{name}.replay"
    if out.exists() and not force:
        return {"ok": False, "reason": "exists", "file": str(out)}
    out.unlink(missing_ok=True)
    wait = wait if wait is not None else (frames / fps * 3 + 60 if frames > 0 else 3600)
    d = _session(runner, project, folder, {"mode": "record", "name": name, "frames": frames if frames > 0 else -1,
                                           "fps": fps, "seed": seed}, [out], wait, timeout, None, sleep, clock)
    if d["file"]:
        rec = _load(out)
        d.update(reason="recorded", frames=(rec["end"] or 0) + 1, input_changes=len(rec["input"]), marks=len(rec["marks"]))
    d["ok"] = d["reason"] == "recorded"
    return d


def run(runner, project: str | None, name: str, stop_at: int | None = None, tolerance: float = 1e-4,
        wait: float | None = None, timeout: float = 120,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
    root = _project(runner, project)
    if root is None:
        return {"ok": False, "reason": "no_project"}
    folder = root / ".playcal" / "replays"
    try:
        rec = _load(folder / f"{name}.replay")
    except ReplayError as e:
        return {"ok": False, "reason": "no_recording" if not (folder / f"{name}.replay").exists() else "bad_file",
                "problem": str(e)}
    result, paused = folder / f"{name}.result", folder / f"{name}.paused"
    result.unlink(missing_ok=True)
    paused.unlink(missing_ok=True)
    request = {"mode": "replay", "name": name, "stop_at": stop_at if stop_at is not None else -1}
    wait = wait if wait is not None else ((rec["end"] or 0) / rec["fps"] * 3 + 60)
    pausing = stop_at is not None
    d = _session(runner, project, folder, request, [paused, result] if pausing else [result], wait, timeout,
                 paused if pausing else None, sleep, clock)
    if d["file"] and d["file"] == str(paused):
        d.update(reason="paused", frame=int(paused.read_text(encoding="utf-8").strip() or -1))
    elif d["file"]:
        d.update(compare(rec, _load(result), tolerance))
        if d["reason"] == "match":
            d["reason"] = observe._console_verdict(d["console"]) or "match"
    d["ok"] = d["reason"] in ("match", "paused")
    return d


def step(runner, project: str | None, frames: int = 1) -> dict:
    eng = engines.wrap(runner)
    res = eng.status(project)
    mode = ((res["data"] or {}).get("result") or {}).get("playMode") if res["success"] else None
    if mode not in ("playing", "paused"):
        return {"ok": False, "reason": "not_playing"}
    for _ in range(frames):
        res = eng.run(["script", "eval", "--code", "UnityEditor.EditorApplication.isPaused = true; "
                                  "UnityEditor.EditorApplication.Step(); return 0;"], project)
        if not res["success"]:
            return {"ok": False, "reason": "eval_failed", "errors": res["errors"]}
    res = eng.run(["script", "eval", "--code", "return Playcal.PlaycalReplay.Frame;"], project)
    frame = ((res["data"] or {}).get("result") or {}).get("result") if res["success"] else None
    return {"ok": True, "reason": "stepped", "steps": frames, "frame": int(frame) if frame not in (None, "") else None}


def _add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["record", "run", "step", "check"],
                   help="record NAME: record a play session. run NAME: replay it and compare. "
                        "step: advance a paused Editor. check NAME or check A B: compare offline")
    p.add_argument("names", nargs="*", help="replay name (files in <project>/.playcal/replays/), or two files for check")
    p.add_argument("--frames", type=int, default=None,
                   help="record: frames to record, 0 = until Play stops (default 600). step: frames to step (default 1)")
    p.add_argument("--fps", type=int, default=60, help="record: fixed frame rate the game steps at (default 60)")
    p.add_argument("--seed", type=int, default=0, help="record: UnityEngine.Random seed (default 0)")
    p.add_argument("--force", action="store_true", help="record: overwrite an existing recording")
    p.add_argument("--stop-at", type=int, default=None, metavar="FRAME",
                   help="run: pause the Editor on this replay frame and stay in Play Mode")
    p.add_argument("--tolerance", type=float, default=1e-4, help="decimal marks may differ by this much, absolute; whole numbers must match exactly (default 1e-4)")
    p.add_argument("--wait", type=float, default=None, help="seconds to wait for the SDK to finish (default from the frame count)")
    p.add_argument("--project-path", help="Unity project folder (default: the open Editor's)")
    p.add_argument("--timeout", type=float, default=120, help="seconds to wait for a ready Editor (default 120)")
    p.add_argument("--json", action="store_true", help="print the digest as JSON")


def _print(out: dict) -> None:
    print(out["reason"] + (f" ({out['problem']})" if out.get("problem") else ""))
    div = out.get("first_divergence")
    if div:
        e, g = div["expected"], div["got"]
        print(f"  frame {div['frame']}: expected {e['name']}={e['value']} at {e['frame']}" if e else
              f"  frame {div['frame']}: expected no more marks", end="")
        print(f", got {g['name']}={g['value']} at {g['frame']}" if g else ", got none")
    elif "marks_recorded" in out:
        print(f"  {out['marks_replayed']}/{out['marks_recorded']} marks, frames 0..{out['end_replayed']}"
              f" of 0..{out['end_recorded']}")
    con = out.get("console") or {}
    for e in con.get("errors") or []:
        print(f"  {e.get('code')}: {e.get('message')}" + (f"  at {e['file']}:{e.get('line')}" if e.get("file") else ""))
    for k in ("frames", "frame", "file"):
        if out.get(k) is not None:
            print(f"  {k}: {out[k]}")
    if out["reason"] == "no_sdk":
        print("  the request was never read: add com.playcal.sdk to the project and route input through PlaycalInput")


def _run(args: argparse.Namespace) -> int:
    try:
        if args.action == "check":
            if len(args.names) == 1:
                root = _project(loop.unity_runner, args.project_path or ".")
                if root is None:
                    raise ReplayError("check NAME needs a Unity project (--project-path); or pass two files")
                folder = root / ".playcal" / "replays"
                pair = folder / f"{args.names[0]}.replay", folder / f"{args.names[0]}.result"
            elif len(args.names) == 2:
                pair = Path(args.names[0]), Path(args.names[1])
            else:
                raise ReplayError("check takes a replay name or two files")
            out = compare(_load(pair[0]), _load(pair[1]), args.tolerance)
            out["ok"] = out["reason"] == "match"
        elif args.action == "step":
            out = step(loop.unity_runner, args.project_path, args.frames or 1)
        elif len(args.names) != 1:
            raise ReplayError(f"{args.action} takes one replay name")
        elif args.action == "record":
            out = record(loop.unity_runner, args.project_path, args.names[0], 600 if args.frames is None else args.frames,
                         args.fps, args.seed, args.force, args.wait, args.timeout)
        else:
            out = run(loop.unity_runner, args.project_path, args.names[0], args.stop_at, args.tolerance, args.wait,
                      args.timeout)
    except ReplayError as e:
        out = {"ok": False, "reason": "bad_file", "problem": str(e)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        _print(out)
    code = EXIT.get(out["reason"], loop.EXIT.get(out["reason"], 1))
    history.record("replay", args.action, out, code, " ".join(args.names or []))
    return code


MODULE = Module(name="replay", summary="record input through the SDK, replay it as a regression test with fixed frame rate and seed, step frames",
                add_args=_add_args, run=_run, engines=("unity",))
