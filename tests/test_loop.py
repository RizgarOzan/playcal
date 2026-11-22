"""loop against a scripted fake of the Unity CLI: each test plays back the JSON envelopes a real Editor would give."""
import json

from playcal import loop
from playcal.cli import main


def status(*states, project="C:/Games/Lab"):
    return {"success": bool(states), "data": {"instances": [{"project": project, "state": s} for s in states]}}


class Fake:
    """Plays back `unity status` envelopes in order (the last one repeats) and records every call.
    `job batch` runs each op through the same replies and answers the way Pipeline 0.10 does."""

    def __init__(self, statuses, command=(0, {"success": True, "data": {"ok": 1}}), pipeline=None):
        self.statuses, self.command, self.calls = list(statuses), command, []
        self.pipeline = pipeline or {"data": {"instances": []}}

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "status":
            return 0, self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if args[0] == "pipeline":
            return 0, self.pipeline
        if args[1] == "job":
            items = []
            for op in json.loads(args[args.index("--operations") + 1]):
                rc, env = self.reply(op["command"].split()[0])
                ok = bool(env.get("success")) and rc == 0
                items.append({"command": op["command"], "success": ok, "result": (env.get("data") or {}).get("result"),
                              **({} if ok else {"error": (env.get("errors") or [{}])[0]})})
            return 0, {"success": True, "data": {"result": {"results": items}}}
        return self.reply(args[1])

    def reply(self, name):
        if not isinstance(self.command, dict):
            return self.command
        if name not in self.command:
            return 1, {"success": False, "errors": [{"code": "UNKNOWN_COMMAND", "message": name}]}
        reply = self.command[name]  # a list plays back in order
        return reply.pop(0) if isinstance(reply, list) and len(reply) > 1 else reply[0] if isinstance(reply, list) else reply


def fast(**kw):
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    return dict(sleep=sleep, clock=lambda: t["now"], **kw)


def test_one_ready_poll_is_not_enough_a_reload_can_flicker():
    fake = Fake([status("ready"), status("compiling"), status("ready"), status("ready")])
    out = loop.settle(fake, **fast())
    assert out["settled"] and out["states_seen"] == ["ready", "compiling", "ready"]
    assert sum(c[0] == "status" for c in fake.calls) == 4


def test_safe_mode_reported_as_itself():
    pipeline = {"data": {"instances": [{"project": "C:/Games/Lab", "safeMode": {"detected": True}}]}}
    out = loop.settle(Fake([status()], pipeline=pipeline), project="C:/Games/Lab", **fast())
    assert out["reason"] == "safe_mode" and "compile errors" in out["hint"]


def test_no_editor_after_timeout():
    out = loop.settle(Fake([status()]), timeout=3, **fast())
    assert out["reason"] == "no_editor" and not out["settled"]


def test_stuck_editor_times_out_with_last_state():
    out = loop.settle(Fake([status("busy")]), timeout=3, **fast())
    assert out["reason"] == "timeout" and out["state"] == "busy"


def test_two_editors_need_project_path():
    two = {"data": {"instances": [{"project": "C:/A", "state": "ready"}, {"project": "C:/B", "state": "ready"}]}}
    assert loop.settle(Fake([two]), **fast())["reason"] == "ambiguous"
    assert loop.settle(Fake([two]), project="C:/B", **fast())["settled"]


def test_run_settles_on_both_sides_and_passes_project():
    fake = Fake([status("ready")])
    out = loop.run_command(fake, "save_scene", ["--name", "x"], project="C:/Games/Lab", **fast())
    assert out["ok"] and out["reason"] == "ready" and out["result"]["data"] == {"ok": 1}
    cmd = [c for c in fake.calls if c[0] == "command"]
    assert cmd == [["command", "editor", "status", "--project-path", "C:/Games/Lab"],
                   ["command", "save_scene", "--name", "x", "--project-path", "C:/Games/Lab"],
                   ["command", "editor", "status", "--project-path", "C:/Games/Lab"]]
    assert sum(c[0] == "status" for c in fake.calls) == 4  # editor status answered nothing useful, so settle polled


def test_run_does_not_fire_into_a_busy_editor():
    fake = Fake([status("compiling")])
    out = loop.run_command(fake, "save_scene", [], timeout=2, **fast())
    assert not out["ok"] and out["reason"] == "timeout"
    assert not any(c[:2] == ["command", "save_scene"] for c in fake.calls)


def test_failed_command_surfaces_error_code():
    fake = Fake([status("ready")], command=(6, {"success": False, "errors": [{"code": "NOT_FOUND", "message": "no"}]}))
    out = loop.run_command(fake, "delete_gameobject", [], **fast())
    assert out["reason"] == "command_failed" and out["result"]["errors"][0]["code"] == "NOT_FOUND"


def test_cli_without_unity_installed(monkeypatch, capsys):
    monkeypatch.setenv("PLAYCAL_UNITY", "definitely-not-a-unity-binary")
    assert main(["loop", "settle", "--timeout", "0", "--json"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["reason"] == "no_editor"


def test_cli_params_after_double_dash(monkeypatch):
    seen = {}

    def fake_run(runner, name, params, project, timeout, **kw):
        seen.update(name=name, params=params, timeout=timeout)
        return {"reason": "ready", "before": {"states_seen": ["ready"]}}

    monkeypatch.setattr(loop, "run_command", fake_run)
    assert main(["loop", "run", "save_scene", "--timeout", "5", "--", "--name", "x"]) == 0
    assert seen == {"name": "save_scene", "params": ["--name", "x"], "timeout": 5.0}


def scene(*names):
    return 0, {"success": True, "data": {"command": "get_hierarchy", "target": {"port": 7800},
                                         "result": {"objects": [{"name": n} for n in names]}}}


def editor(status="ready", compiling=False, reloading=False, play="stopped"):
    return 0, {"success": True, "data": {"result": {"status": status, "compiling": compiling, "playMode": play,
                                                    "domainReloadInProgress": reloading, "codeLoadCount": 46}}}


def test_verify_catches_a_write_that_did_not_land():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}),
                                            "get_hierarchy": scene("Main Camera")})
    out = loop.run_command(fake, "create", ["--name", "Probe"], verify=["get_hierarchy"],
                           expect=["result.objects.*.name=Probe"], **fast())
    assert not out["ok"] and out["reason"] == "verify_failed" and out["fault"] == "task"
    assert out["checks"][0]["actual"] == ["Main Camera"]


def test_verify_and_diff_on_success():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}), "editor": editor(),
                                            "get_hierarchy": [scene("Main Camera"), scene("Main Camera", "Probe")]})
    out = loop.run_command(fake, "create", [], verify=["get_hierarchy"], expect=["result.objects.*.name=Probe"],
                           want_diff=True, **fast())
    assert out["ok"] and out["fault"] is None and out["checks"][0]["ok"]
    assert out["diff"] == [{"path": "result.objects[name=Probe]", "added": {"name": "Probe"}}]  # no echo noise
    order = [c[1] for c in fake.calls if c[0] == "command"]
    assert order == ["job", "create", "job"]  # the snapshot rides in the ready check before the write
    assert not any(c[0] == "status" for c in fake.calls)


def test_quiet_write_reads_back_in_the_same_call_as_the_status_check():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}), "editor": editor(),
                                            "get_hierarchy": scene("Probe")})
    out = loop.run_command(fake, "create", [], verify=["get_hierarchy"], expect=["result.objects.*.name=Probe"], **fast())
    assert out["ok"] and out["after"]["via"] == "editor status" and out["after"]["code_load_count"] == 46
    assert not any(c[0] == "status" for c in fake.calls) and out["before"]["via"] == "editor status"
    ops = json.loads(next(c for c in fake.calls if c[:2] == ["command", "job"])[4])
    assert ops == [{"command": "editor status", "params": {}}, {"command": "get_hierarchy", "params": {}}]


def test_quiet_write_without_verify_checks_editor_status_once():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}), "editor": editor()})
    out = loop.run_command(fake, "create", [], **fast())
    assert out["ok"] and out["after"]["via"] == "editor status"
    assert [c[1:3] for c in fake.calls if c[0] == "command"] == [["editor", "status"], ["create"], ["editor", "status"]]


def test_busy_or_silent_editor_status_falls_back_to_settle():
    for reply in (editor(compiling=True), editor(reloading=True), editor(status="busy"), None):
        command = {"create": (0, {"success": True, "data": {}}), "get_hierarchy": scene("Probe")}
        if reply:
            command["editor"] = reply
        fake = Fake([status("ready")], command=command)
        out = loop.run_command(fake, "create", [], verify=["get_hierarchy"], expect=["result.objects.*.name=Probe"],
                               **fast())
        assert out["ok"] and "via" not in out["after"] and sum(c[0] == "status" for c in fake.calls) == 4
        assert [c[1] for c in fake.calls if c[0] == "command"][-1] == "get_hierarchy"  # read again after settle


def test_refused_write_checks_editor_status_once_and_skips_the_read():
    refused = (1, {"success": False, "errors": [{"code": "COMMAND_FAILED", "message": "in play mode"}]})
    fake = Fake([status("ready")], command={"create": refused, "editor": editor(), "get_hierarchy": scene("Probe")})
    out = loop.run_command(fake, "create", [], verify=["get_hierarchy"], expect=["result.objects.*.name=Probe"], **fast())
    assert out["reason"] == "command_failed" and out["fault"] == "task" and out["after"]["via"] == "editor status"
    assert [c[1:3] for c in fake.calls if c[0] == "command"] == [["editor", "status"], ["create"], ["editor", "status"]]
    gone = Fake([status("ready"), status("ready"), status("none")], command={"create": refused})
    out = loop.run_command(gone, "create", [], timeout=1, **fast())
    assert out["reason"] == "command_failed" and "via" not in out["after"]  # no answer: settle says why


def test_write_that_starts_a_reload_keeps_the_settle_path():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {"requiresRecompile": True}}),
                                            "editor": editor()})
    out = loop.run_command(fake, "create", [], **fast())
    after_write = fake.calls[fake.calls.index(["command", "create"]) + 1:]
    assert out["ok"] and "via" not in out["after"] and not any(c[1] in ("job", "editor") for c in after_write
                                                                if c[0] == "command")


def test_compile_keeps_the_settle_path():
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}), "editor": editor()})
    fake_rc = fake.__call__

    def runner(args):
        return (0, {"success": True, "data": {"status": "skipped"}}) if args[0] == "recompile" else fake_rc(args)

    out = loop.run_command(runner, "create", [], compile=True, **fast())
    assert out["ok"] and "via" not in out["after"] and out["compile"]["ok"]


def test_expect_without_verify_checks_the_command_result():
    out = loop.run_command(Fake([status("ready")]), "x", [], expect=["ok=1"], **fast())
    assert out["ok"] and out["checks"] == [{"expect": "ok=1", "ok": True, "actual": 1}]


def test_fault_split_infra_vs_task():
    assert loop.run_command(Fake([status()]), "x", [], timeout=1, **fast())["fault"] == "infra"
    cli_broke = Fake([status("ready")], command=(127, {"success": False, "errors": [{"code": "UNITY_CLI_MISSING"}]}))
    assert loop.run_command(cli_broke, "x", [], **fast())["fault"] == "infra"
    game_broke = Fake([status("ready")], command=(1, {"success": False, "errors": [{"code": "NOT_FOUND"}]}))
    assert loop.run_command(game_broke, "x", [], **fast())["fault"] == "task"


def test_cli_verify_flags_reach_run_command(monkeypatch):
    seen = {}

    def fake_run(runner, name, params, project, timeout, **kw):
        seen.update(kw)
        return {"reason": "verify_failed", "before": {"states_seen": ["ready"]}}

    monkeypatch.setattr(loop, "run_command", fake_run)
    rc = main(["loop", "run", "create", "--verify", "get_hierarchy --depth 1", "--expect", "a=1", "--expect", "b",
               "--diff", "--json"])
    assert rc == 5 and seen == {"verify": ["get_hierarchy", "--depth", "1"], "expect": ["a=1", "b"], "want_diff": True,
                       "checkpoint": None, "compile": False, "reload_wait": None}


def test_checkpoint_before_write_and_undo_hint_on_failure(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x")
    fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}),
                                            "get_hierarchy": scene("Main Camera")})
    out = loop.run_command(fake, "create", [], verify=["get_hierarchy"], expect=["result.objects.*.name=Probe"],
                           checkpoint=str(tmp_path), **fast())
    assert out["checkpoint"]["saved"] and out["undo"] == f"playcal checkpoint restore {out['checkpoint']['id']}"


def test_checkpoint_outside_git_does_not_block_the_command(tmp_path):
    out = loop.run_command(Fake([status("ready")]), "x", [], checkpoint=str(tmp_path / "nowhere"), **fast())
    assert out["ok"] and "error" in out["checkpoint"] and "undo" not in out


def test_play_mode_refuses_the_write_after_one_editor_status_call():
    for play, state in (("playing", "playing"), ("paused", "ready")):
        fake = Fake([status("ready")], command={"create": (0, {"success": True, "data": {}}),
                                                "editor": editor(status=state, play=play)})
        out = loop.run_command(fake, "create", ["--name", "Probe"], **fast())
        assert out["reason"] == "play_mode" and out["fault"] == "task" and "editor.playmode" in out["hint"]
        assert len(fake.calls) == 1 and fake.calls[0][:3] == ["command", "editor", "status"]  # no settle, no write
        assert loop.EXIT["play_mode"] == 4


def test_play_mode_commands_still_run_in_play_mode():
    fake = Fake([status("ready")], command={"editor.playmode": (0, {"success": True, "data": {}}),
                                            "editor": editor(play="playing")})
    out = loop.run_command(fake, "editor.playmode", ["stop"], **fast())
    assert out["ok"] and ["command", "editor.playmode", "stop"] == fake.calls[1][:3]


def test_envelope_reads_cli_1_0_bare_output_as_the_old_envelope():
    # CLI 1.0 prints `unity command` results bare; errors go to stderr only
    one = '{"hierarchyPath": "/Probe", "type": "GameObject"}'
    assert loop._envelope(0, one, "") == {"success": True, "data": {"result": json.loads(one)}}
    assert loop._envelope(0, "", "")["data"] == {"result": None}
    assert loop._envelope(0, '{"a": 1}\n{"a": 2}\n', "")["data"]["result"] == [{"a": 1}, {"a": 2}]
    bad = loop._envelope(6, "", "error: Command 'component set_field' failed: Input string was not in a correct format.\n")
    assert bad["success"] is False and bad["errors"][0]["code"] == "COMMAND_FAILED_6"
    assert loop._fault("command_failed", bad) == "task"  # a bad value is the agent's fault, not the Editor's


def test_envelope_passes_the_old_envelope_through():
    old = {"success": False, "command": "command", "data": None, "errors": [{"code": "EDITOR_NOT_RUNNING", "message": "x"}]}
    assert loop._envelope(1, json.dumps(old), "") == old
    assert loop._envelope(0, "not json", "")["errors"][0]["code"] == "UNITY_CLI_BAD_JSON"
