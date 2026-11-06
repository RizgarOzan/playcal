import json
import random

import pytest

from playcal.calibrate import calibrate, expit, fit, leave_one_out, logit, predict
from playcal.cli import main
from playcal.report import summarize


def events(level, n, k, start=0):
    """n one-attempt players on a level, the first k pass."""
    out = []
    for i in range(n):
        inst = f"{start + i:08x}"
        base = {"v": 0, "install_id": inst, "session_id": "sess1", "game": "g", "game_version": "1", "level": level, "attempt": 1}
        out.append({**base, "ts": "2026-11-04T10:00:00Z", "type": "level_start"})
        out.append({**base, "ts": "2026-11-04T10:01:00Z", "type": "level_complete" if i < k else "level_fail"})
    return out


def stats(spec):
    """spec: {level: (n, k)} -> calibrate's input dict."""
    evs, start = [], 0
    for level, (n, k) in spec.items():
        evs += events(level, n, k, start)
        start += n
    return {(s.game_version, s.level): s for s in summarize(evs)}


def test_fit_recovers_a_known_line():
    # human logit = 0.5 + 0.8 * bot logit, no level scatter, lots of humans
    rnd = random.Random(1)
    pts = []
    for x in [-2, -1, -0.5, 0, 0.5, 1, 2, 2.5]:
        n = 4000
        k = sum(rnd.random() < expit(0.5 + 0.8 * x) for _ in range(n))
        y, v = logit(k, n)
        pts.append((x, y, v))
    m = fit(pts)
    assert m["b"] == pytest.approx(0.8, abs=0.08)
    assert m["a"] == pytest.approx(0.5, abs=0.08)
    assert m["tau2"] < 0.02


def test_scatter_widens_the_interval():
    tight = [(x, 0.5 * x, 0.01) for x in (-2, -1, 0, 1, 2)]
    loose = [(x, 0.5 * x + d, 0.01) for x, d in zip((-2, -1, 0, 1, 2), (0.6, -0.7, 0.5, -0.6, 0.4))]
    _, lo_t, hi_t = predict(fit(tight), 0.0)
    m = fit(loose)
    _, lo_l, hi_l = predict(m, 0.0)
    assert m["tau2"] > 0.1
    assert hi_l - lo_l > 3 * (hi_t - lo_t)


def test_needs_three_levels():
    with pytest.raises(ValueError, match="at least 3"):
        fit([(0, 0, 1), (1, 1, 1)])
    with pytest.raises(ValueError, match="all the same"):
        fit([(0, 0, 1), (0, 1, 1), (0, 2, 1)])


def test_leave_one_out_on_a_clean_line():
    pts = [(x, 0.5 + 0.8 * x, 0.05) for x in (-2, -1, 0, 1, 2, 3)]
    loo = leave_one_out(pts)
    assert loo == {"levels": 6, "inside_interval": 6, "median_abs_error": pytest.approx(0, abs=1e-6)}
    assert leave_one_out(pts[:3]) is None


def test_calibrate_predicts_unplayed_levels():
    bot = stats({"01": (50, 45), "02": (50, 35), "03": (50, 25), "04": (50, 15), "05": (50, 5), "06": (50, 30)})
    # humans a bit worse than the bots on every level; nobody played 06
    human = stats({"01": (30, 25), "02": (30, 18), "03": (30, 11), "04": (30, 6), "05": (30, 1)})
    out = calibrate(human, bot)
    rows = {r["level"]: r for r in out["levels"]}
    assert out["model"]["calibration_levels"] == 5
    assert not rows["06"]["calibration"] and "human_pass_rate" not in rows["06"]
    assert rows["06"]["ci"][0] < rows["06"]["predicted"] < rows["06"]["ci"][1]
    # 06 sits between 02 and 03 for bots, so its prediction lands between their human rates
    assert rows["03"]["human_pass_rate"] < rows["06"]["predicted"] < rows["02"]["human_pass_rate"]
    assert out["leave_one_out"]["levels"] == 5


def test_min_attempts_drops_thin_levels():
    bot = stats({"01": (40, 30), "02": (40, 20), "03": (40, 10), "04": (40, 5)})
    human = stats({"01": (10, 8), "02": (10, 5), "03": (10, 2), "04": (3, 0)})
    assert calibrate(human, bot)["model"]["calibration_levels"] == 3
    with pytest.raises(ValueError):
        calibrate(human, bot, min_attempts=11)


def write(path, evs):
    path.write_text("".join(json.dumps(e) + "\n" for e in evs), encoding="utf-8")
    return str(path)


def test_cli_json_and_table(tmp_path, capsys):
    b = write(tmp_path / "bot.jsonl", events("01", 40, 36) + events("02", 40, 24, 40) + events("03", 40, 12, 80)
              + events("04", 40, 4, 120) + events("05", 40, 20, 160))
    h = write(tmp_path / "human.jsonl", events("01", 20, 16) + events("02", 20, 10, 20) + events("03", 20, 4, 40)
              + events("04", 20, 1, 60))
    assert main(["calibrate", "--human", h, "--bot", b, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [r["level"] for r in out["levels"]] == ["01", "02", "03", "04", "05"]
    assert out["model"]["b"] > 0 and out["leave_one_out"]["levels"] == 4
    assert main(["calibrate", "--human", h, "--bot", b]) == 0
    text = capsys.readouterr().out
    assert "leave-one-out: " in text and "05" in text


def test_cli_too_few_levels_exits_2(tmp_path, capsys):
    b = write(tmp_path / "bot.jsonl", events("01", 20, 10) + events("02", 20, 5, 20))
    h = write(tmp_path / "human.jsonl", events("01", 10, 5) + events("02", 10, 2, 10))
    assert main(["calibrate", "--human", h, "--bot", b, "--json"]) == 2
    assert "at least 3" in json.loads(capsys.readouterr().out)["error"]
    assert main(["calibrate", "--human", str(tmp_path / "missing.jsonl"), "--bot", b]) == 2


def test_cli_says_when_every_event_is_rejected(tmp_path, capsys):
    bad = [{**e, "session_id": "x"} for e in events("01", 5, 2)]
    b = write(tmp_path / "bot.jsonl", events("01", 20, 10))
    assert main(["calibrate", "--human", write(tmp_path / "h.jsonl", bad), "--bot", b, "--json"]) == 2
    assert "rejected" in json.loads(capsys.readouterr().out)["error"]


def test_targets_exit_1_with_the_level(tmp_path, capsys):
    b = write(tmp_path / "bot.jsonl", events("01", 40, 36) + events("02", 40, 24, 40) + events("03", 40, 12, 80)
              + events("04", 40, 4, 120))
    h = write(tmp_path / "human.jsonl", events("01", 20, 16) + events("02", 20, 10, 20) + events("03", 20, 4, 40)
              + events("04", 20, 1, 60))
    t = tmp_path / "targets.json"
    t.write_text(json.dumps({"01": [40, 95], "04": [40, 60]}), encoding="utf-8")
    assert main(["calibrate", "--human", h, "--bot", b, "--targets", str(t)]) == 1
    text = capsys.readouterr().out
    assert "level 04: predicted " in text and "target 40%-60%" in text and "level 01:" not in text
    assert main(["report", h, "--targets", str(t), "--json"]) == 1
    miss = json.loads(capsys.readouterr().out)["targets_missed"]
    assert [m["level"] for m in miss] == ["04"] and miss[0]["pass rate"] == 0.05
    t.write_text(json.dumps({"04": [0, 0.6]}), encoding="utf-8")
    assert main(["report", h, "--targets", str(t)]) == 0
