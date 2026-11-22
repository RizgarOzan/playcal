"""Live benchmark: a naive agent vs playcal loop against a real, open Unity Editor.

Same shape as sim.py, but every call goes to the `unity` CLI and "landed" is read back from the
Editor afterwards. Each scenario puts the Editor in a state, then one arm sends one write:

    clean          Editor idle.
    during_reload  a script was touched and recompiled; the write goes out while the domain
                   reload has taken the Pipeline server away.
    play_mode      Editor is in Play Mode, where edit-time writes are refused.
    bad_value      the write itself is wrong (a string into a float field).

naive fires the write and trusts `success`. playcal goes through loop.run_command with a
read-back check. Objects it creates are named BenchLive* and deleted at the end; the touched
script lives in Assets/Editor/TemporaryGeneratedScripts and is removed too.

    python bench/live.py --project-path C:/path/to/Project [--repeat 3] [--json]
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from playcal import checks, loop

RUN = loop.unity_runner


def cmd(project, *argv):
    return loop._call(RUN, list(argv), project)


def gone(project) -> bool:
    _, env = RUN(["status"])
    return not loop._pick((env.get("data") or {}).get("instances") or [], project)[0]


def exists(project, name) -> bool:
    loop.settle(RUN, project, 180)
    r = cmd(project, "gameobject", "find", "--name", name)
    # CLI 1.0 returns the matches bare (one object or a list); older ones {gameObjects, count}
    return r["success"] and f"/{name}" in checks.lookup(r["data"], "result.**.hierarchyPath")


def touch_script(project) -> Path:
    d = Path(project, "Assets/Editor/TemporaryGeneratedScripts")
    d.mkdir(parents=True, exist_ok=True)
    f = d / "BenchTouch.cs"
    f.write_text(f"public static class BenchTouch {{ public const int N = {random.randrange(1 << 30)}; }}\n", encoding="utf-8")
    return f


def setup(scenario, project, name) -> dict:
    """Puts the Editor in the scenario's state. Returns what was observed, e.g. whether the
    reload window was actually caught."""
    loop.settle(RUN, project, 180)
    if scenario == "bad_value":
        cmd(project, "gameobject", "create", "--name", name)
    if scenario == "during_reload":
        touch_script(project)
        cmd(project, "script", "recompile")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 60:
            if gone(project):
                return {"window_caught": True}
        return {"window_caught": False}
    if scenario == "play_mode":
        cmd(project, "editor.playmode", "start")
        loop.settle(RUN, project, 120)
    return {}


def teardown(scenario, project):
    if scenario == "play_mode":
        loop.settle(RUN, project, 120)
        cmd(project, "editor.playmode", "stop")


def write_for(scenario, name):
    if scenario == "bad_value":
        return ["component", "set_field", "--target", name, "--component", "Transform",
                "--field", "m_LocalPosition.x", "--value", "abc"]
    return ["gameobject", "create", "--name", name]


def landed(scenario, project, name) -> bool:
    if scenario != "bad_value":
        return exists(project, name)
    return False  # the write is nonsense; nothing correct can land


def naive(project, write, name):
    r = cmd(project, *write)
    return {"reported_ok": r["success"], "said": (r["errors"] or [{}])[0].get("message", "ok")[:80]}


def playcal(project, write, name):
    out = loop.run_command(RUN, write[0], write[1:], project=project, timeout=180,
                           verify=["gameobject", "find", "--name", name],
                           expect=[f"result.**.hierarchyPath=/{name}"])
    said = out["reason"] if not out.get("fault") else f"{out['reason']}/{out['fault']}"
    return {"reported_ok": out["ok"], "said": said}


def read_cost(project) -> dict:
    """Bytes an agent reads to learn the names of the bench objects: the whole hierarchy vs a
    playcal query. A rough token proxy, on whatever scene is open."""
    loop.settle(RUN, project, 180)
    full = subprocess.run(["unity", "command", "scene", "get_hierarchy", "--project-path", project, "--json",
                           "--no-banner"], capture_output=True, text=True, encoding="utf-8").stdout
    q = subprocess.run([sys.executable, "-m", "playcal", "query", "--unity", "scene get_hierarchy",
                        "--project-path", project, "--from", "result.**", "--where", "name~BenchLive",
                        "--select", "name", "--json"], capture_output=True, text=True, encoding="utf-8").stdout
    return {"full_hierarchy_bytes": len(full), "query_bytes": len(q)}


SCENARIOS = ["clean", "during_reload", "play_mode", "bad_value"]


def run(project, repeat, scenarios=SCENARIOS) -> dict:
    rows, made = [], []
    for i in range(repeat):
        for scenario in scenarios:
            for agent, fn in (("naive", naive), ("playcal", playcal)):
                name = f"BenchLive_{scenario}_{agent}_{i}"
                made.append(name)
                seen = setup(scenario, project, name)
                t0 = time.monotonic()
                r = fn(project, write_for(scenario, name), name)
                secs = round(time.monotonic() - t0, 1)
                teardown(scenario, project)
                ok = landed(scenario, project, name)
                rows.append({"scenario": scenario, "agent": agent, "run": i, "landed": ok, **r,
                             "false_success": r["reported_ok"] and not ok, "seconds": secs, **seen})
                print(json.dumps(rows[-1]), file=sys.stderr, flush=True)
    cost = read_cost(project)
    for name in made:
        if exists(project, name):
            cmd(project, "gameobject", "delete", "--target", name)
    d = Path(project, "Assets/Editor/TemporaryGeneratedScripts")
    for f in (d / "BenchTouch.cs", d / "BenchTouch.cs.meta"):
        f.unlink(missing_ok=True)
    return {"rows": rows, "read_cost": cost}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--project-path", required=True)
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.add_argument("--scenario", action="append", choices=SCENARIOS, metavar="NAME",
                   help="run only this scenario; repeat the flag for more (default: all)")
    a = p.parse_args(argv)
    scenarios = a.scenario or SCENARIOS
    out = run(a.project_path, a.repeat, scenarios)
    if a.json:
        print(json.dumps(out))
        return 0
    print(f"{'scenario':<15}{'agent':<9}{'landed':>7}{'said ok':>9}{'FALSE OK':>10}{'median s':>10}")
    for scenario in scenarios:
        for agent in ("naive", "playcal"):
            rs = [r for r in out["rows"] if r["scenario"] == scenario and r["agent"] == agent]
            secs = sorted(r["seconds"] for r in rs)[len(rs) // 2]
            print(f"{scenario:<15}{agent:<9}{sum(r['landed'] for r in rs):>4}/{len(rs)}"
                  f"{sum(r['reported_ok'] for r in rs):>6}/{len(rs)}{sum(r['false_success'] for r in rs):>7}/{len(rs)}"
                  f"{secs:>10}")
    caught = [r["window_caught"] for r in out["rows"] if "window_caught" in r]
    if caught:
        print(f"reload window caught in {sum(caught)}/{len(caught)} during_reload runs")
    c = out["read_cost"]
    print(f"read cost: full hierarchy {c['full_hierarchy_bytes']} bytes, playcal query {c['query_bytes']} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
