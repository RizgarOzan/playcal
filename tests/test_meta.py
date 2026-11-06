import json
from argparse import Namespace

from playcal import meta

G1, G2, G3 = "a" * 32, "b" * 32, "c" * 32


def _meta(p, guid):
    p.write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")


def _project(tmp_path):
    a = tmp_path / "Assets"
    a.mkdir()
    (a / "Player.cs").write_text("public class Player : MonoBehaviour {}", encoding="utf-8")
    _meta(a / "Player.cs.meta", G1)
    (a / "Main.unity").write_text(
        "%YAML 1.1\n  m_Script: {fileID: 11500000, guid: " + G1 + ", type: 3}\n", encoding="utf-8")
    _meta(a / "Main.unity.meta", G2)
    (tmp_path / "Library" / "PackageCache").mkdir(parents=True)
    return tmp_path


def _kinds(out, key):
    return sorted(x["kind"] for x in out[key])


def test_clean_project_is_ok(tmp_path):
    out = meta.check(_project(tmp_path))
    assert out["ok"] and out["errors"] == [] and out["warnings"] == []


def test_duplicate_guid(tmp_path):
    p = _project(tmp_path)
    (p / "Assets" / "Copy.cs").write_text("public class Copy : MonoBehaviour {}", encoding="utf-8")
    _meta(p / "Assets" / "Copy.cs.meta", G1)
    out = meta.check(p)
    assert _kinds(out, "errors") == ["duplicate_guid"]


def test_moved_without_meta(tmp_path):
    p = _project(tmp_path)
    (p / "Assets" / "Sub").mkdir()
    (p / "Assets" / "Player.cs").rename(p / "Assets" / "Sub" / "Player.cs")
    out = meta.check(p)
    assert _kinds(out, "errors") == ["moved_without_meta"]
    assert out["errors"][0]["guid"] == G1


def test_missing_script_and_warnings(tmp_path):
    p = _project(tmp_path)
    s = p / "Assets" / "Main.unity"
    s.write_text(s.read_text() + "  m_Script: {fileID: 11500000, guid: " + G3 + ", type: 3}\n")
    (p / "Assets" / "New.txt").write_text("x")
    _meta(p / "Assets" / "Gone.png.meta", "d" * 32)
    (p / "Assets" / "Bad.cs").write_text("public class Other : MonoBehaviour {}")
    _meta(p / "Assets" / "Bad.cs.meta", "e" * 32)
    out = meta.check(p)
    assert _kinds(out, "errors") == ["missing_script"]
    assert _kinds(out, "warnings") == ["class_name", "missing_meta", "orphan_meta"]


def test_missing_script_is_warning_without_package_cache(tmp_path):
    p = _project(tmp_path)
    (p / "Library" / "PackageCache").rmdir()
    s = p / "Assets" / "Main.unity"
    s.write_text(s.read_text() + "  m_Script: {fileID: 11500000, guid: " + G3 + ", type: 3}\n")
    out = meta.check(p)
    assert out["ok"] and _kinds(out, "warnings") == ["missing_script"]


def test_bad_meta_and_builtin_guid(tmp_path):
    p = _project(tmp_path)
    (p / "Assets" / "M.mat").write_text("%YAML 1.1\n  m_Shader: {fileID: 46, guid: 0000000000000000f000000000000000, type: 0}\n")
    (p / "Assets" / "M.mat.meta").write_text("fileFormatVersion: 2\n")
    out = meta.check(p)
    assert _kinds(out, "errors") == ["bad_meta"] and out["warnings"] == []


def test_run_exit_codes(tmp_path, capsys):
    p = _project(tmp_path)
    assert meta._run(Namespace(project=str(p), limit=20, json=True)) == 0
    assert json.loads(capsys.readouterr().out)["ok"]
    _meta(p / "Assets" / "X.cs.meta", G1)
    (p / "Assets" / "X.cs").write_text("")
    assert meta._run(Namespace(project=str(p), limit=20, json=False)) == 1
    assert "duplicate_guid" in capsys.readouterr().out
    assert meta._run(Namespace(project=str(tmp_path / "nope"), limit=20, json=False)) == 2
