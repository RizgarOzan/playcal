import json

from playcal import calibrate, playtest
from playcal.ingest import check_lines

RED = {"ok": False, "reason": "errors", "fault": "game", "checks": [{"ok": True}],
       "console": {"errors_seen": 1, "errors": [{"code": "NullReferenceException", "message": "boom",
                                                 "file": "Assets/P.cs", "line": 4}]}, "played_ms": {"max": 3000}}
GREEN = {"ok": True, "reason": "clean", "fault": None, "checks": [{"ok": True}],
         "console": {"errors_seen": 0}, "played_ms": {"max": 3000}}
INFRA = {"ok": False, "reason": "no_editor", "fault": "infra"}


def _seq(*runs):
    it = iter(runs)
    return lambda: next(it)


def test_flaky_two_of_three_is_inconclusive():
    out, _ = playtest.playtest(_seq(RED, GREEN, RED), 3, ["hp=3"])
    assert out["verdict"] == "inconclusive" and playtest.EXIT["inconclusive"] == 7
    assert out["checklist"][0] == {"item": "no console errors", "pass": 1, "fail": 2, "skip": 0}
    assert out["first_error"]["file"] == "Assets/P.cs"


def test_deterministic_three_of_three_is_red():
    out, _ = playtest.playtest(_seq(RED, RED, RED), 3)
    assert out["verdict"] == "red" and out["reason"] == "errors"


def test_infra_runs_are_skipped_not_counted():
    out, _ = playtest.playtest(_seq(GREEN, INFRA, GREEN), 3, ["hp=3"])
    assert out["verdict"] == "clean" and out["counted"] == 2
    assert out["checklist"][1] == {"item": "hp=3", "pass": 2, "fail": 0, "skip": 1}
    out, _ = playtest.playtest(_seq(INFRA), 1)
    assert out["verdict"] == "skipped"


def test_events_are_pc0_and_calibrate_reads_them(tmp_path):
    _, runs = playtest.playtest(_seq(RED, GREEN, RED, INFRA), 4)
    evs = playtest.events(runs, "demo", "1.0", "4")
    f = tmp_path / "bot.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    res = check_lines(("bot", line) for line in f.read_text(encoding="utf-8").splitlines())
    assert not res.rejected and len(res.accepted) == len(evs)
    stats = calibrate._stats([str(f)])
    row = stats[("1.0", "4")]
    assert row.attempts == 3 and row.attempts_passed == 1
