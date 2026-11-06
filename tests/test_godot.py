import json
from pathlib import Path

from playcal import observe
from playcal.engines import godot

PUSH_ERROR = """Godot Engine v4.7.2.stable.official
ERROR: health went negative
   at: push_error (core/variant/variant_utility.cpp:1092)
   GDScript backtrace (most recent call first):
       [0] _ready (res://main.gd:4)
ERROR: health went negative
   at: push_error (core/variant/variant_utility.cpp:1092)
   GDScript backtrace (most recent call first):
       [0] _ready (res://main.gd:4)
"""
PARSE = """SCRIPT ERROR: Parse Error: Expected end of statement after expression, found "Identifier" instead.
   at: GDScript::reload (res://player.gd:7)
ERROR: Failed to load script "res://player.gd" with error "Parse error".
   at: load (modules/gdscript/gdscript.cpp:2936)
"""
TREE = {"name": "Main", "type": "Node2D", "path": ".", "children": [{"name": "Hero", "type": "Sprite2D", "path": "Hero"}]}


def test_summarize_push_error_points_at_script_line():
    s = godot.summarize(PUSH_ERROR)
    assert not s["ok"] and s["error_kinds"] == 1 and s["error_total"] == 2
    e = s["first_cause"]
    assert (e["file"], e["line"], e["message"]) == ("main.gd", 4, "health went negative")


def test_summarize_parse_error_is_compile_and_first_cause():
    s = godot.summarize(PARSE)
    assert s["first_cause"]["code"] == "ParseError"
    assert (s["first_cause"]["file"], s["first_cause"]["line"]) == ("player.gd", 7)
    assert godot.summarize("Godot Engine v4.7.2\n")["ok"]


def fake(text, data, rc=0, calls=None):
    def run(args, timeout):
        if calls is not None:
            calls.append(args)
        out = next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), None)
        if out:
            Path(out).write_text(json.dumps(data), encoding="utf-8")
        return rc, text
    return run


def project(tmp_path, imported=True):
    (tmp_path / "project.godot").write_text("", encoding="utf-8")
    if imported:
        (tmp_path / ".godot").mkdir()
    return str(tmp_path)


def test_check_reports_compile_error_then_ready(tmp_path):
    p = project(tmp_path)
    bad = godot.Godot(fake(PARSE, {"checked": 2})).check(p)
    assert bad["reason"] == "compile_error" and bad["compile"]["errors"][0]["file"] == "player.gd"
    assert godot.Godot(fake("", {"checked": 2})).check(p)["reason"] == "ready"


def test_missing_godot_and_missing_project(tmp_path):
    assert godot.Godot(fake("", {})).check(str(tmp_path))["reason"] == "no_editor"
    out = godot.Godot(lambda a, t: (127, "ERROR: GODOT_MISSING: x")).check(project(tmp_path))
    assert out["reason"] == "no_editor" and out["console"]["first_cause"]["code"] == "GODOT_MISSING"


def test_fresh_project_is_imported_first(tmp_path):
    calls = []
    godot.Godot(fake("", {"checked": 0}, calls=calls)).check(project(tmp_path, imported=False))
    assert "--import" in calls[0] and "-s" in calls[1]


def test_observe_godot_errors_then_clean_and_same_tree(tmp_path):
    p = project(tmp_path)
    data = {"scene": "res://main.tscn", "before": TREE, "after": TREE, "played_ms": 3000}
    red = observe.observe_godot(godot.Godot(fake(PUSH_ERROR, data)), p)
    assert red["reason"] == "errors" and observe.EXIT[red["reason"]] == 1
    assert red["console"]["errors"][0]["file"] == "main.gd"
    green = observe.observe_godot(godot.Godot(fake("", data)), p, expect=["children.0.name=Hero"])
    assert green["reason"] == "clean" and green["diff"]["same"] and green["checks"][0]["ok"]


def test_observe_godot_without_tree_is_read_failed(tmp_path):
    out = observe.observe_godot(godot.Godot(fake("", {"error": "the project has no main scene; pass --scene"}, rc=2)),
                                project(tmp_path))
    assert out["reason"] == "read_failed" and "main scene" in out["read_errors"][0]["message"]
