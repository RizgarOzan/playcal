import io
import json
import subprocess
import sys
from pathlib import Path

from playcal import surfaces
from playcal.registry import load_modules

FIX = Path(__file__).parent / "fixtures"
MODS = {m.name: m for m in load_modules()}


def test_loop_schema_comes_from_argparse():
    s = surfaces.tool_schema(MODS["loop"])
    assert s["required"] == ["action"] and s["properties"]["action"]["enum"] == ["settle", "run"]
    assert s["properties"]["expect"]["type"] == "array" and s["properties"]["diff"]["type"] == "boolean"
    assert s["properties"]["timeout"] == {**s["properties"]["timeout"], "type": "number", "default": 120}
    assert "json" not in s["properties"]


def test_argv_puts_list_positionals_after_double_dash():
    argv = surfaces.argv_for(MODS["loop"], {"action": "run", "name": "save_scene", "params": ["--name", "x"],
                                            "expect": ["a=1", "b"], "diff": True, "timeout": 5})
    assert argv == ["--timeout", "5", "--expect", "a=1", "--expect", "b", "--diff", "run", "save_scene",
                    "--", "--name", "x"]


def rpc(*msgs):
    out = io.StringIO()
    surfaces.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_mcp_handshake_list_and_call():
    replies = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "playcal_logs", "arguments": {"path": str(FIX / "editor_sample.log")}}},
                  {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "playcal_report", "arguments": {"files": [str(FIX / "events-20.jsonl")]}}})
    assert [r["id"] for r in replies] == [1, 2, 3, 4]  # the notification gets no reply
    assert replies[0]["result"]["serverInfo"]["name"] == "playcal"
    assert "playcal doctor" in replies[0]["result"]["instructions"]
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"playcal_loop", "playcal_logs", "playcal_report", "playcal_ingest"} <= names
    logs = replies[2]["result"]
    assert logs["isError"] and json.loads(logs["content"][0]["text"])["first_cause"]["code"] == "CS0246"
    assert not replies[3]["result"]["isError"]
    json.loads(replies[3]["result"]["content"][0]["text"])


def test_mcp_errors():
    bad_tool, bad_method, bad_args = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playcal_nope"}},
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "playcal_loop", "arguments": {"action": "fly"}}})
    assert bad_tool["error"]["code"] == -32602 and bad_method["error"]["code"] == -32601
    assert bad_args["result"]["isError"] and "invalid choice" in bad_args["result"]["content"][0]["text"]


def test_mcp_over_a_real_pipe_prints_nothing_but_json():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "playcal_logs", "arguments": {"path": str(FIX / "editor_sample.log")}}}]
    p = subprocess.run([sys.executable, "-m", "playcal", "mcp"], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, timeout=60)
    lines = p.stdout.splitlines()
    assert p.returncode == 0 and len(lines) == 2 and all(json.loads(x)["jsonrpc"] == "2.0" for x in lines)


def test_skill_md_lists_every_module():
    md = surfaces.skill_md()
    assert md.startswith("---\nname: playcal\n")
    for name in MODS:
        assert f"## playcal {name}" in md
    assert "`--verify`" in md
    assert "## Which command when" in md and md.index("## Which command when") < md.index("## playcal ")


def test_shipped_skill_and_plugin_match_the_code():
    """skills/playcal/SKILL.md is what `npx skills add` and the Claude plugin install; regenerate with
    `playcal skill > skills/playcal/SKILL.md` after changing a module or GUIDE."""
    from playcal import __version__
    root = Path(__file__).resolve().parents[1]
    assert (root / "skills/playcal/SKILL.md").read_text(encoding="utf-8").replace("\r\n", "\n") == surfaces.skill_md() + "\n"
    assert json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"] == __version__
