import json

from playcal import cli, history, ui
from playcal.visual import write_png


def png(path, value):
    write_png(path, 2, 2, [bytes([value, 0, 0, 255] * 2)] * 2)


def test_visual_runs_land_in_history_and_on_the_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    png(tmp_path / "a.png", 0)
    png(tmp_path / "b.png", 255)
    assert cli.main(["visual", "save", "hud", "a.png", "--dir", "base"]) == 0
    assert cli.main(["visual", "check", "hud", "a.png", "--dir", "base"]) == 0
    assert cli.main(["visual", "check", "hud", "b.png", "--dir", "base"]) == 1
    entries = history.read(history.path())
    assert [(e["tool"], e["exit"]) for e in entries] == [("visual", 0), ("visual", 1)]  # save isn't a run
    assert cli.main(["ui", "--out", "page/ui.html"]) == 0
    page = (tmp_path / "page" / "ui.html").read_text(encoding="utf-8")
    assert "2 runs, 1 red" in page and "100.0% of pixels differ" in page
    assert 'src="../base/hud.diff.png"' in page


def test_page_escapes_and_shows_compile_errors_and_undo(tmp_path):
    e = {"t": 1, "tool": "loop", "action": "run", "cmd": "<script>x</script>", "exit": 1,
         "out": {"reason": "compile_failed", "checkpoint": {"id": "abc123"},
                 "compile": {"errors": [{"file": "Assets/P.cs", "line": 7, "code": "CS1002", "message": "; expected"}]},
                 "undo": "playcal checkpoint restore abc123"}}
    page = ui.render([e], tmp_path)
    assert "<script>x" not in page and "&lt;script&gt;" in page
    assert "Assets/P.cs:7 CS1002: ; expected" in page
    assert "checkpoint abc123" in page and "undo: playcal checkpoint restore abc123" in page


def test_bad_lines_are_skipped_and_off_writes_nothing(tmp_path, monkeypatch):
    p = tmp_path / "h.jsonl"
    p.write_text('{"tool": "observe", "exit": 0}\nnot json\n\n', encoding="utf-8")
    assert [e["tool"] for e in history.read(p)] == ["observe"]
    monkeypatch.setenv("PLAYCAL_HISTORY", "off")
    history.record("loop", "run", {}, 0)
    assert history.path() is None


def test_no_history_is_exit_2(tmp_path, capsys):
    assert cli.main(["ui", "--history", str(tmp_path / "none.jsonl")]) == 2
    assert "no history" in capsys.readouterr().err


def test_summary_counts_without_commands_paths_or_output(tmp_path, capsys):
    p = tmp_path / "h.jsonl"
    rows = [
        {"t": 1, "tool": "loop", "action": "run", "cmd": "SecretLevel set", "exit": 0, "out": {"reason": "ready"}},
        {"t": 2, "tool": "loop", "action": "run", "cmd": "x", "exit": 2, "out": {"reason": "play_mode",
                                                                               "project": "C:/Users/me/Game"}},
        {"t": 3, "tool": "observe", "action": "run", "cmd": "Boss", "exit": 1,
         "out": {"verdict": "Assets/Secret.prefab is null"}},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert cli.main(["ui", "--history", str(p), "--summary", "--out", str(tmp_path / "ui.html")]) == 0
    text = capsys.readouterr().out
    assert "3 runs" in text and "loop run: 2 runs (exit 0: 1, exit 2: 1)" in text
    assert "ready 1" in text and "play_mode 1" in text and "other 1" in text
    assert "Secret" not in text and "Users" not in text and "Boss" not in text
    assert not (tmp_path / "ui.html").exists()
