"""Turn analytics exports a studio already has into playcal events (event-v0), so ingest can check them.

Two formats, both checked against their public docs on 2026-11-22:

- GameAnalytics raw data export: one JSON object per event. Progression events have
  data.category == "progression" and data.event_id "Status:Progression01[:02[:03]]",
  Status being Start, Fail or Complete; data.attempt_num is optional.
  https://docs.gameanalytics.com/products-and-features/pipeline-iq/data-export/json-schemas
- Firebase / GA4 BigQuery export, one row per line (e.g. NEWLINE_DELIMITED_JSON): event_name,
  event_timestamp in microseconds UTC, user_pseudo_id, and event_params as key/value records.
  Levels use level_start and level_end (level_name, success). There is no fail event; a fail is
  level_end with success false. https://support.google.com/analytics/answer/7029846

Neither has a playcal-style attempt number everywhere, so attempts are counted per player and level
from the starts, in time order. User and session ids are hashed: the raw ones don't always fit the
schema (GA4 pseudo ids have dots) and playcal doesn't need them in the clear.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable, Iterator

FORMATS = ("gameanalytics", "firebase")
_GA_STATUS = {"Start": "level_start", "Fail": "level_fail", "Complete": "level_complete"}
_SKIP = "skip"  # a row that isn't a level event: dropped without counting as rejected


def _hid(*parts) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:16]


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _gameanalytics(row: dict, game: str | None, version: str | None) -> dict | str:
    d = row.get("data") or {}
    if d.get("category") != "progression":
        return _SKIP
    status, *levels = str(d.get("event_id", "")).split(":")
    if status not in _GA_STATUS or not levels:
        return f"progression event_id {d.get('event_id')!r} is not Start|Fail|Complete:level"
    ts = d.get("client_ts") or row.get("arrival_ts")
    if d.get("user_id") is None or ts is None:
        return "missing data.user_id or a timestamp"
    ev = {"ts": float(ts), "user": d["user_id"], "session": d.get("session_id") or f"{d['user_id']}/{d.get('session_num')}",
          "game": game or str(row.get("game_id", "unknown")), "game_version": version or "unknown",
          "type": _GA_STATUS[status], "level": ":".join(levels), "attempt": d.get("attempt_num")}
    if d.get("score") is not None:
        ev["props"] = {"score": d["score"]}
    return ev


def _param(row: dict, key: str):
    for p in row.get("event_params") or []:
        if p.get("key") == key:
            v = p.get("value") or {}
            for k in ("string_value", "int_value", "double_value", "float_value"):
                if v.get(k) is not None:
                    return v[k]
    return None


def _firebase(row: dict, game: str | None, version: str | None) -> dict | str:
    name = row.get("event_name")
    if name not in ("level_start", "level_end"):
        return _SKIP
    level = _param(row, "level_name")
    if level is None or row.get("user_pseudo_id") is None or row.get("event_timestamp") is None:
        return f"{name} without level_name, user_pseudo_id or event_timestamp"
    if name == "level_start":
        kind = "level_start"
    else:
        success = _param(row, "success")
        if success is None:
            return "level_end without a success param"
        kind = "level_complete" if str(success).lower() in ("true", "1") else "level_fail"
    app = row.get("app_info") or {}
    session = _param(row, "ga_session_id")
    user = row["user_pseudo_id"]
    return {"ts": int(row["event_timestamp"]) / 1e6, "user": user,
            "session": f"{user}/{session if session is not None else row.get('event_date')}",
            "game": game or app.get("id") or "unknown", "game_version": version or app.get("version") or "unknown",
            "type": kind, "level": str(level), "attempt": None}


def convert(lines: Iterable[tuple[str, str]], fmt: str, game: str | None = None,
            version: str | None = None) -> Iterator[tuple[str, str]]:
    """(source, export line) in, (source, event-v0 JSON line) out. Lines that can't be read come out as
    a JSON error marker so ingest rejects them with the reason; rows that aren't level events are dropped."""
    parse = {"gameanalytics": _gameanalytics, "firebase": _firebase}[fmt]
    events, bad = [], []
    for n, (source, line) in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            out = parse(row, game, version) if isinstance(row, dict) else "not a JSON object"
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            out = f"not a {fmt} row: {e}"
        if out == _SKIP:
            continue
        if isinstance(out, str):
            bad.append((source, json.dumps({"__decode_error__": f"line {n}: {out}"})))
        else:
            events.append((out["ts"], n, source, out))
    yield from bad
    starts: dict[tuple, int] = {}
    for _, _, source, e in sorted(events, key=lambda t: (t[0], t[1])):
        key = (e["user"], e["game_version"], e["level"])
        if e["type"] == "level_start":
            starts[key] = starts.get(key, 0) + 1
        attempt = e["attempt"] if e["type"] != "level_start" and e["attempt"] else max(starts.get(key, 0), 1)
        ev = {"v": 0, "ts": _iso(e["ts"]), "install_id": _hid(e["user"]), "session_id": _hid(e["session"]),
              "game": e["game"], "game_version": e["game_version"], "type": e["type"], "level": e["level"],
              "attempt": int(attempt)}
        if "props" in e:
            ev["props"] = e["props"]
        yield source, json.dumps(ev, ensure_ascii=False)
