import json
from pathlib import Path

import pytest

from playcal.cli import main
from playcal.ingest import check_lines, decode_paste, encode_paste

FIXTURE = Path(__file__).parent / "fixtures" / "events-20.jsonl"


def lines(path):
    return [(path.name, line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_18_valid_2_bad():
    res = check_lines(lines(FIXTURE))
    assert len(res.accepted) == 18
    assert len(res.rejected) == 2
    reasons = " | ".join(r["reason"] for r in res.rejected)
    assert "'type' is a required property" in reasons
    assert "attempt" in reasons


def test_level_events_need_level_and_attempt():
    ev = {"v": 0, "ts": "2026-11-04T10:00:00Z", "install_id": "inst_9f3k2a7q", "session_id": "s001",
          "game": "g", "game_version": "1", "type": "level_fail"}
    res = check_lines([("x", json.dumps(ev))])
    assert not res.accepted and "required" in res.rejected[0]["reason"]


def test_unknown_fields_rejected_so_nothing_personal_sneaks_in():
    ev = {"v": 0, "ts": "2026-11-04T10:00:00Z", "install_id": "inst_9f3k2a7q", "session_id": "s001",
          "game": "g", "game_version": "1", "type": "session_start", "email": "a@b.c"}
    assert check_lines([("x", json.dumps(ev))]).rejected


def test_bad_timestamp_rejected():
    ev = {"v": 0, "ts": "yesterday", "install_id": "inst_9f3k2a7q", "session_id": "s001",
          "game": "g", "game_version": "1", "type": "session_start"}
    assert check_lines([("x", json.dumps(ev))]).rejected


def test_paste_roundtrip_inside_chat_noise():
    valid = check_lines(lines(FIXTURE)).accepted
    blob = encode_paste(valid)
    chat = f"[10:42] Ayşe: işte sonucum\n[10:42] Ayşe: {blob}\n[10:43] Ayşe: 05 zordu"
    res = check_lines(decode_paste(chat))
    assert res.accepted == valid and not res.rejected


def test_broken_paste_reported_not_crashing():
    res = check_lines(decode_paste("PC0:AAAAbroken"))
    assert not res.accepted and "paste blob broken" in res.rejected[0]["reason"]


def test_cli_writes_files_and_json_summary(tmp_path, capsys):
    out, rej = tmp_path / "ok.jsonl", tmp_path / "bad.jsonl"
    assert main(["ingest", str(FIXTURE), "--out", str(out), "--rejected", str(rej), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert (summary["accepted"], summary["rejected"]) == (18, 2)
    assert len(out.read_text().splitlines()) == 18 and len(rej.read_text().splitlines()) == 2


def test_cli_missing_file_exit_2(tmp_path):
    assert main(["ingest", str(tmp_path / "nope.jsonl"), "--out", str(tmp_path / "o"), "--rejected", str(tmp_path / "r")]) == 2


def test_modules_listed_from_registry(capsys):
    assert main(["modules", "--json"]) == 0
    names = [m["name"] for m in json.loads(capsys.readouterr().out)]
    assert "ingest" in names


# Export rows shaped after the documented fields (neither doc page has a sample row); see playcal/convert.py.
def _ga(user, ts, event_id, **extra):
    return json.dumps({"arrival_ts": ts + 5, "game_id": 1234, "data": {
        "user_id": user, "session_id": "a1b2c3d4-0000-4000-8000-000000000001", "session_num": 1,
        "client_ts": ts, "category": "progression", "event_id": event_id, **extra}})


def _fb(user, ts_s, name, level, success=None, session=1700000000):
    params = [{"key": "level_name", "value": {"string_value": level}},
              {"key": "ga_session_id", "value": {"int_value": session}}]
    if success is not None:
        params.append({"key": "success", "value": {"int_value": int(success)}})
    return json.dumps({"event_date": "20261122", "event_timestamp": int(ts_s * 1e6), "event_name": name,
                       "user_pseudo_id": user, "event_params": params, "app_info": {"id": "com.x.game", "version": "1.4"}})


def test_gameanalytics_progression_becomes_events_with_counted_attempts():
    from playcal.convert import convert
    rows = [_ga("u1", 1000, "Start:World1:Level3"), _ga("u1", 1030, "Fail:World1:Level3", score=40),
            _ga("u1", 1040, "Start:World1:Level3"), _ga("u1", 1090, "Complete:World1:Level3"),
            json.dumps({"arrival_ts": 1, "data": {"category": "business", "user_id": "u1"}}),  # not a level event
            _ga("u1", 1100, "Restart:World1:Level3")]
    res = check_lines(convert([("ga.json", r) for r in rows], "gameanalytics"))
    assert [(e["type"], e["attempt"]) for e in res.accepted] == [
        ("level_start", 1), ("level_fail", 1), ("level_start", 2), ("level_complete", 2)]
    first = res.accepted[0]
    assert first["level"] == "World1:Level3" and first["game"] == "1234" and first["ts"] == "1970-01-01T00:16:40.000Z"
    assert res.accepted[1]["props"] == {"score": 40}
    assert len(res.rejected) == 1 and "Restart" in res.rejected[0]["reason"]


def test_gameanalytics_attempt_num_wins_over_the_count():
    from playcal.convert import convert
    res = check_lines(convert([("ga", _ga("u1", 5, "Fail:L1", attempt_num=4))], "gameanalytics"))
    assert res.accepted[0]["attempt"] == 4


def test_firebase_level_end_success_maps_to_complete_or_fail_out_of_order():
    from playcal.convert import convert
    rows = [_fb("123.456", 20, "level_end", "L1", success=True), _fb("123.456", 10, "level_end", "L1", success=False),
            _fb("123.456", 0, "level_start", "L1"), _fb("123.456", 15, "level_start", "L1"),
            _fb("123.456", 30, "screen_view", "L1"), _fb("123.456", 40, "level_end", "L1")]
    res = check_lines(convert([("fb.json", r) for r in rows], "firebase"))
    assert [(e["type"], e["attempt"]) for e in res.accepted] == [
        ("level_start", 1), ("level_fail", 1), ("level_start", 2), ("level_complete", 2)]
    e = res.accepted[0]
    assert (e["game"], e["game_version"]) == ("com.x.game", "1.4")
    assert "." not in e["install_id"] and e["install_id"] == res.accepted[3]["install_id"]
    assert len(res.rejected) == 1 and "success" in res.rejected[0]["reason"]


def test_ingest_from_cli_feeds_report(tmp_path, capsys):
    src = tmp_path / "fb.jsonl"
    src.write_text("\n".join([_fb("p1", 0, "level_start", "L1"), _fb("p1", 9, "level_end", "L1", success=True),
                              _fb("p2", 0, "level_start", "L1"), _fb("p2", 9, "level_end", "L1", success=False)]),
                   encoding="utf-8")
    out = tmp_path / "ok.jsonl"
    assert main(["ingest", str(src), "--from", "firebase", "--game-version", "2.0", "--out", str(out),
                 "--rejected", str(tmp_path / "bad.jsonl"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] == 4
    assert all(json.loads(l)["game_version"] == "2.0" for l in out.read_text(encoding="utf-8").splitlines())
    assert main(["report", str(out), "--json"]) == 0
    assert "L1" in capsys.readouterr().out
