import pytest

from playcal import engines


def test_unity_sends_the_cli_commands_modules_used_to_spell_out():
    sent = []
    eng = engines.get("unity", lambda argv: (sent.append(argv), (0, {"success": True}))[1])
    eng.play("P"), eng.stop(None), eng.screenshot("a.png", None)
    assert sent == [["command", "editor.playmode", "start", "--project-path", "P"],
                    ["command", "editor.playmode", "stop"],
                    ["command", "capture", "game_view", "--output-path", "a.png"]]
    assert eng.console_since({"cursor": 7, "session": "s"})[-4:] == ["--since", "7", "--since-session", "s"]


def test_wrap_keeps_an_engine_and_wraps_a_runner():
    eng = engines.get()
    assert engines.wrap(eng) is eng
    assert engines.wrap(lambda a: (0, {})).name == "unity"
    assert engines.get("godot").name == "godot"
    with pytest.raises(ValueError, match="cryengine"):
        engines.get("cryengine")
