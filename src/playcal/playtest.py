"""playcal playtest: run observe N times and say whether the game is red, green or flaky.

Each --expect is a checklist row (pass / fail / skip per run), plus one row for console errors.
A run that broke for infra reasons (no Editor, unreadable console) is skipped, not counted.
  every counted run red   -> the red reason (exit 1)
  some red, some green    -> inconclusive (exit 7): a flaky failure, run more or look closer
  every counted run green -> clean (exit 0)
--events writes each run as one PC0 attempt (level_start then level_complete or level_fail), so
`playcal calibrate --bot` and `playcal report` read playtests like any other bot run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
from pathlib import Path
from typing import Callable, Sequence

from . import history
from .registry import Module

EXIT = {"clean": 0, "red": 1, "inconclusive": 7, "skipped": 3}


def _row_state(run: dict, i: int) -> str:
    if run.get("fault") == "infra" or "checks" not in run:
        return "skip"
    return "pass" if run["checks"][i]["ok"] else "fail"


def aggregate(runs: Sequence[dict], expect: Sequence[str] = ()) -> dict:
    counted = [r for r in runs if r.get("fault") != "infra"]
    red = [r for r in counted if not r["ok"]]
    verdict = ("skipped" if not counted else "clean" if not red else "red" if len(red) == len(counted)
               else "inconclusive")
    rows = [{"item": "no console errors",
             **{s: sum(1 for r in runs if (("skip" if r.get("fault") == "infra" else
                                           "fail" if (r.get("console") or {}).get("errors_seen") else "pass") == s))
                for s in ("pass", "fail", "skip")}}]
    for i, e in enumerate(expect):
        states = [_row_state(r, i) for r in runs]
        rows.append({"item": e, **{s: states.count(s) for s in ("pass", "fail", "skip")}})
    out = {"ok": verdict == "clean", "verdict": verdict, "runs": len(runs), "counted": len(counted),
           "red": len(red), "checklist": rows,
           "reasons": sorted({r["reason"] for r in red})}
    first = next((r for r in red if (r.get("console") or {}).get("errors")), None)
    if first:
        out["first_error"] = first["console"]["errors"][0]
    if verdict in ("red", "inconclusive"):
        out["reason"] = verdict if verdict == "inconclusive" else out["reasons"][0]
    else:
        out["reason"] = verdict
    return out


def events(runs: Sequence[dict], game: str, version: str, level: str, now=None) -> list[dict]:
    """One PC0 session; each counted run is one attempt. Infra-skipped runs leave no attempt."""
    now = now or dt.datetime.now(dt.timezone.utc)
    base = {"v": 0, "install_id": "bot_" + secrets.token_hex(6), "session_id": "pt_" + secrets.token_hex(4),
            "game": game, "game_version": version}
    t = [0]

    def ev(type_: str, **kw) -> dict:
        t[0] += 1
        ts = (now + dt.timedelta(milliseconds=t[0])).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {**base, "ts": ts, "type": type_, **kw}

    out, attempt = [ev("session_start")], 0
    for r in runs:
        if r.get("fault") == "infra":
            continue
        attempt += 1
        ms = int(((r.get("played_ms") or {}).get("max")) or 0)
        out.append(ev("level_start", level=level, attempt=attempt))
        out.append(ev("level_complete" if r["ok"] else "level_fail", level=level, attempt=attempt, duration_ms=ms,
                      props={"reason": r["reason"]}))
    out.append(ev("session_end"))
    return out


def playtest(run_once: Callable[[], dict], n: int, expect: Sequence[str] = ()) -> tuple[dict, list[dict]]:
    runs = [run_once() for _ in range(max(1, n))]
    return aggregate(runs, expect), runs


def print_report(out: dict) -> None:
    print(f"{out['verdict']}: {out['red']}/{out['counted']} runs red" +
          (f", {out['runs'] - out['counted']} skipped" if out["runs"] != out["counted"] else ""))
    for row in out["checklist"]:
        print(f"  {row['pass']} pass  {row['fail']} fail  {row['skip']} skip   {row['item']}")
    e = out.get("first_error")
    if e:
        print(f"  first error: {e.get('code')}: {e.get('message')}" + (f"  at {e['file']}:{e.get('line')}" if e.get("file") else ""))


def _add_args(p: argparse.ArgumentParser) -> None:
    from .observe import _add_args as observe_args
    observe_args(p)
    p.set_defaults(repeat=3)
    p.add_argument("--events", metavar="FILE", help="write the runs as PC0 events (JSONL) for report and calibrate")
    p.add_argument("--level", default="playtest", help="level name in the events (default playtest)")
    p.add_argument("--game", default="game", help="game name in the events (default game)")
    p.add_argument("--game-version", default="dev", help="game_version in the events (default dev)")


def _run(args: argparse.Namespace) -> int:
    from .observe import run_once
    out, runs = playtest(lambda: run_once(args), args.repeat, args.expect)
    if getattr(args, "events", None):
        path = Path(args.events)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for e in events(runs, args.game, args.game_version, args.level):
                f.write(json.dumps(e) + "\n")
        out["events"] = str(path)
    print(json.dumps(out, ensure_ascii=False) if args.json else "", end="\n" if args.json else "")
    if not args.json:
        print_report(out)
    code = EXIT[out["verdict"]]
    history.record("playtest", "run", out, code, f"x{args.repeat}")
    return code


MODULE = Module(name="playtest", summary="run observe N times: a checklist of pass/fail/skip per run, red vs flaky (inconclusive), runs saved as PC0 events for calibrate",
                add_args=_add_args, run=_run, engines=("unity", "godot"))
