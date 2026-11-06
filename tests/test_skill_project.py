from playcal import cli, surfaces


def test_detects_engine(tmp_path):
    assert surfaces.detect_engine(tmp_path) is None
    (tmp_path / "project.godot").write_text("")
    assert surfaces.detect_engine(tmp_path) == "godot"
    u = tmp_path / "u"
    (u / "Assets").mkdir(parents=True)
    (u / "ProjectSettings").mkdir()
    assert surfaces.detect_engine(u) == "unity"


def test_writes_project_skill_with_red_gate(tmp_path, capsys):
    (tmp_path / "project.godot").write_text("")
    assert cli.main(["skill", "--project", str(tmp_path)]) == 0
    md = (tmp_path / ".claude/skills/playcal/SKILL.md").read_text(encoding="utf-8")
    assert "Godot project" in md and "loop settle --engine godot" in md
    assert md.index("## This project") < md.index("## Which command when")


def test_unknown_project_exits_2(tmp_path):
    assert cli.main(["skill", "--project", str(tmp_path)]) == 2
    assert cli.main(["skill", "--project", str(tmp_path), "--engine", "unity"]) == 0
    assert "playcal doctor" in (tmp_path / ".claude/skills/playcal/SKILL.md").read_text(encoding="utf-8")


def test_frontmatter_description_has_no_yaml_colon():
    for engine in surfaces.PROJECT_GUIDE:
        desc = surfaces.skill_md(engine).split("\n")[2]
        assert ": " not in desc.removeprefix("description: ")
