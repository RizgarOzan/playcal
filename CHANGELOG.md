# Changelog

## 0.2.2 (2026-11-08)

- Plain-text and `--json` output no longer crash on a Windows console that can't encode a file name (e.g. a combining cedilla under cp1254); unencodable characters print as `?`.
- `loop settle --engine godot` without a Godot binary prints the `GODOT_MISSING` hint in plain text too, not only with `--json`.

## 0.2.1 (2026-11-07)

- `playcal doctor` ends with a link to the issue tracker.

## 0.2.0 (2026-11-07)

- `playcal assets check`: texture import settings (type, mipmaps, Read/Write, compression, max size) against a folder policy, plus the real image size, offline.
- `calibrate --targets` and `report --targets`: a pass-rate band per level; a level outside its band is named and the command exits 1.
- README: the Unreal adapter is postponed until it can be run against a real Unreal Editor.
- `playcal audio check`: audio import settings (`.meta` / Godot `.import`) against a folder policy, plus WAV loudness (LUFS), true peak and clipping, offline with the stdlib.
- `playcal skill --project DIR` writes a project SKILL.md with the engine (Unity or Godot, detected) and its red gate at the top.
- `playcal playtest` and `observe --repeat N`: run observe several times, print a pass/fail/skip checklist, and call a sometimes-red result `inconclusive` (exit 7). `--events` writes the runs as PC0 events that `calibrate` and `report` read.
- Godot adapter: `loop settle --engine godot`, `observe --engine godot` and `snap --godot` run Godot headless with a probe script and report parse errors and `push_error` as file:line. Set `PLAYCAL_GODOT` when `godot` is not on PATH.
- `playcal ui`: loop, observe, replay and visual check now append to `.playcal/history.jsonl`, and `playcal ui` draws it as one static HTML page with errors as file:line, checkpoints and diff images. `PLAYCAL_HISTORY=off` turns the history off.
- Claude Code plugin and marketplace manifest, so `/plugin install playcal@playcal` adds the skill and the MCP server. The skill also installs with `npx skills add RizgarOzan/playcal`.
- Unity commands moved behind an engine adapter (`playcal/engines/`), the first step toward Godot and Unreal. No behaviour change.
- `playcal meta`: an offline check for .meta and GUID damage (copied or left-behind .meta files, missing scripts, class names that don't match the file) before Unity imports it.

## 0.1.3 (2026-11-06)

- `playcal skill` now opens with a short "which command when" section, so an agent reading the SKILL.md knows the order: doctor, loop for every write, logs for the first cause. The MCP server sends the same lines as `instructions` on initialize.
- `visual` reads 16-bit PNGs too (grey, RGB, RGBA); they are compared at 8 bits, the high byte of each sample (#1).
- `logs` reads `adb logcat` dumps: the threadtime prefix is stripped and only the Unity tag is counted (#3).
- `bench/live.py --scenario NAME` runs a single scenario (#2).
- `doctor` prints the Unity CLI version on its `unity cli` line, so a bug report carries it.

## 0.1.2 (2026-11-05)

- README only: the MCP Registry name is `io.github.RizgarOzan/playcal`, with the GitHub login's case. 0.1.1 shipped it lowercase, so the registry could not match the PyPI package. No code change.

## 0.1.1 (2026-11-05)

- `loop run`: the ready check before the write is one `editor status` call now, with the `--diff` snapshot in the same batch, instead of two `unity status` polls and a separate read. A quiet write is 3 CLI calls instead of 4 (idle write ~7 s live, was ~15 s; bad value ~5 s, was ~11 s). The polls stay as the fallback when the Editor is busy, silent, or about to reload.
- Listed in the MCP Registry as `io.github.RizgarOzan/playcal`; the release workflow publishes `server.json` after PyPI.

## 0.1.0 (2026-11-05)

First public release. Everything here runs without Unity except `loop`, `observe`, `replay`, `doctor`'s Editor checks and the Unity SDK; those were checked on a live Unity 6000.3 Editor with Pipeline 0.10.0-exp.1.

- `loop`: settle, run one Editor command, settle again, read back and assert, one JSON digest with `fault: infra|task`. After a write that starts no reload, one `job batch` call checks `editor status` and does the read-back, so a write is 4 CLI calls instead of 6 (idle write ~15 s live, was ~24 s). `--diff` no longer reports the request echo as a change.
- `logs`, `query`, `snap`, `checkpoint`, `lock`, `doctor`.
- `observe`: press Play, run the game a few seconds, report console errors, `--expect` checks and the scene diff; a Game view screenshot only when the run goes red. Reads go through `job batch` and Play Mode is checked with `editor status`, so a clean run is 7 CLI calls (~33 s live) instead of 16 (~70 s); `played_ms` says how long the game really ran.
- `replay`: record input through the SDK, play it back with a fixed frame rate and seed, report the first frame where the marks differ; stop on a frame and step from there.
- `baseline` (perf and memory), `visual` (screenshot gate), `tests` (flaky history, quarantine, affected test assemblies).
- `ingest`, `report`, `calibrate`: an event schema with no room for personal data, per-level pass rates with Wilson intervals, and human pass-rate predictions from bot runs.
- `playcal mcp` (stdio MCP server) and `playcal skill` (SKILL.md), both generated from the module registry.
- Unity SDK `com.playcal.sdk`: level events, paste-back result line, frame and memory recorder, screenshots, input recording and replay.
- `bench/sim.py` (scripted regression guard) and `bench/live.py` (naive CLI vs `loop` on a real Editor).
