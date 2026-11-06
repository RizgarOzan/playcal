---
name: playcal
description: Game-dev toolkit for agents. Use to read playtest results, summarize Unity logs, and run Unity Editor commands safely (wait until ready, run, verify). Every command takes --json.
---

# playcal

Run `playcal <command> --json` and read the JSON. Exit code 0 means ok.

## Which command when

- Before anything in a Unity project: `playcal doctor`. It says whether the CLI and an open Editor are reachable.
- Any Editor write (create, set_field, save_scene, recompile): `playcal loop run NAME -- ARGS` with `--verify` and `--expect`. Never call `unity command` directly for a write; a success during a domain reload is not a landed change.
- Scripts changed: `playcal loop run ... --compile`, then `playcal logs --since "Reloading assemblies"` for the first cause.
- Something is red in the console: `playcal logs` first, it collapses repeats and names the first cause.
- Big command output: `playcal query` to keep only the fields you need instead of reading the whole hierarchy.
- Moved, renamed, copied or deleted files under Assets/ outside the Editor: `playcal meta` before Unity imports them.
- Before a risky step: `playcal checkpoint save`; `playcal loop run --checkpoint` does it for you.
- Does it still run: `playcal observe` (press Play, diff the scene, collect errors), `playcal visual check` for a screenshot diff.
- Playtest data: `ingest` validates, `report` summarizes, `calibrate` predicts human pass rates from bot runs.

## playcal baseline

keep a perf baseline (p95 frame time, GC, memory) and fail only when a run is worse by more than noise.

- `action`: save NAME FILE: keep a run as the baseline. check NAME FILE: compare a new run. show NAME
- `name`: baseline name, e.g. level4-android
- `source`: samples JSON file, or - for stdin (default)
- `--dir`: where baselines live (default .playcal/baselines)
- `--percentile`: percentile to compare (default 95)
- `--tolerance`: allowed slowdown in percent (default 10)
- `--floor`: ignore slowdowns smaller than this in absolute units, e.g. frame_ms=0.5. Repeatable
- `--metric`: only these metrics (default: all in both)

## playcal calibrate

predict each level's human pass rate from bot runs, calibrated on a small human panel, with 95% intervals.

- `--human`: event JSONL from the human panel (accepted.jsonl from ingest)
- `--bot`: event JSONL from bot runs, same schema
- `--min-attempts`: human attempts a level needs to count for calibration (default 5)

## playcal checkpoint

save project files to a private git ref before each step, and restore any save.

- `action`: save the files now, list saves, diff a save against now, restore a save
- `target`: save: a message. diff/restore: checkpoint id, latest, or ~N (N saves back)
- `--repo`: folder inside the git repo (default: current folder)
- `--limit`: how many saves to list (default 20)

## playcal doctor

check Python, git, the Unity CLI, open Editors and the SDK before a run.

- `--project`: Unity project folder to check (default: whatever Editors are open)

## playcal ingest

validate gameplay events (JSONL or pasted PC0 blobs) against the event schema.

- `files`: JSONL files, or '-' for stdin
- `--paste`: inputs are free text containing PC0: blobs
- `--out`: where valid events go (default: accepted.jsonl)
- `--rejected`: where invalid lines go, with a reason

## playcal lock

lock scenes and assets so several agents don't edit the same file at once.

- `action`: take PATH...: lock all or none. release PATH...: unlock yours. list: live locks. check PATH...: exit 1 if someone else holds one
- `paths`: scene or asset paths, e.g. Assets/Scenes/Level4.unity
- `--owner`: who holds the lock (default $PLAYCAL_OWNER)
- `--ttl`: how long a lock lasts, e.g. 90s, 30m, 2h (default 30m)
- `--force`: release: also drop someone else's lock
- `--dir`: where locks live (default .playcal/locks)

## playcal logs

summarize a Unity log: collapse repeats, show the first cause.

- `path`: log file, or - for stdin (default: this machine's Unity Editor.log)
- `--since`: only read after the last line containing TEXT, e.g. "Reloading assemblies"
- `--limit`: most distinct errors to list (default 20)

## playcal loop

run a Unity Editor command safely: wait until ready, run, wait again, one digest.

- `action`: settle: wait for a ready Editor. run: settle, run, settle
- `name`: Editor command for `run` (see `unity command`)
- `params`: passed to the command unchanged; put them after --, e.g. run save_scene -- --name x
- `--project-path`: Unity project folder; needed when several Editors are open
- `--timeout`: seconds to wait for a ready Editor (default 120)
- `--verify`: read command run after the write, e.g. "get_hierarchy --depth 1"
- `--expect`: check on the --verify read (or the command result). PATH=VALUE, PATH!=VALUE, PATH~TEXT, or bare PATH (exists). * matches any list item: objects.*.name=Probe. Repeatable
- `--diff`: run --verify before the write too and report only what changed
- `--checkpoint`: save files with playcal checkpoint before the write; on failure the digest says how to undo
- `--compile`: also run `unity recompile` and fail with the compile errors; the Editor says ready even when scripts don't build
- `--reload-wait`: after the write, wait this long for a reload to start before trusting ready (default: 20 when the command says it needs a recompile, else 0)

## playcal meta

find .meta and GUID damage (copied, moved or missing .meta files, missing scripts) before Unity imports it, without an Editor.

- `--project`: Unity project folder (the one with Assets/), default .
- `--limit`: text output: lines per kind (default 20, 0 = all)

## playcal observe

press Play, run the game a few seconds, diff the scene and collect console errors; screenshot only on red.

- `--read`: Editor read to diff before and during play (default "scene get_hierarchy")
- `--seconds`: how long to let the game run before the second read (default 3)
- `--no-play`: don't touch Play Mode; diff two reads --seconds apart
- `--expect`: check on the read during play, same syntax as playcal loop --expect. Repeatable
- `--shots`: where the red-run screenshot goes, relative to the Unity project (default .playcal/observe)
- `--name`: screenshot file name without .png (default observe)
- `--no-shot`: never take a screenshot
- `--ignore`: field to leave out of the diff, repeatable (default: instanceId, isDirty)
- `--limit`: most diff entries to list (default 50)
- `--project-path`: Unity project folder; needed when several Editors are open
- `--timeout`: seconds to wait for a ready Editor (default 120)

## playcal query

pick a list, filter it and keep only the fields you need from a big JSON result.

- `source`: JSON file, or - for stdin (default). Ignored with --unity
- `--unity`: read from a Unity Editor command instead, e.g. "get_hierarchy --depth 3"
- `--project-path`: Unity project folder, with --unity
- `--from`: the list to read, e.g. data.objects; ** walks nested children at any depth
- `--where`: keep items where EXPR holds: name~Enemy, active=true, tag!=Untagged. Repeatable
- `--select`: field to keep per item. Repeatable
- `--limit`: most items to return (default 50)

## playcal replay

record input through the SDK, replay it as a regression test with fixed frame rate and seed, step frames.

- `action`: record NAME: record a play session. run NAME: replay it and compare. step: advance a paused Editor. check NAME or check A B: compare offline
- `names`: replay name (files in <project>/.playcal/replays/), or two files for check
- `--frames`: record: frames to record, 0 = until Play stops (default 600). step: frames to step (default 1)
- `--fps`: record: fixed frame rate the game steps at (default 60)
- `--seed`: record: UnityEngine.Random seed (default 0)
- `--force`: record: overwrite an existing recording
- `--stop-at`: run: pause the Editor on this replay frame and stay in Play Mode
- `--tolerance`: decimal marks may differ by this much, absolute; whole numbers must match exactly (default 1e-4)
- `--wait`: seconds to wait for the SDK to finish (default from the frame count)
- `--project-path`: Unity project folder (default: the open Editor's)
- `--timeout`: seconds to wait for a ready Editor (default 120)

## playcal report

per-level pass rate, attempts and quit rate with 95% Wilson intervals.

- `files`: event JSONL files (usually accepted.jsonl from ingest)

## playcal snap

save a JSON read under a name and diff two saves, with ids that change between sessions left out.

- `action`: save NAME: store a read. diff A [B]: what changed from A to B (or to a live --unity read). list
- `refs`: save: NAME [FILE or - for stdin]. diff: A [B], each a saved name or a .json file
- `--unity`: read from the Editor, e.g. "scene get_hierarchy"
- `--project-path`: Unity project folder, with --unity
- `--dir`: where saves live (default .playcal/snaps)
- `--ignore`: field to leave out of the diff, repeatable (default: instanceId). --ignore '' keeps everything
- `--limit`: most changes to list (default 50)

## playcal tests

test result history: spot flaky tests, fail CI only on real failures, run only affected test assemblies.

- `action`: record FILE...: add results to the history. check FILE...: fail only on non-flaky failures, then record. flaky: list tests that passed and failed on one commit. affected [PATH...]: test assemblies a change can break (default: git diff --base)
- `files`: NUnit3 (Unity -testResults) or JUnit XML files; for affected, changed paths
- `--base`: affected: git ref to diff against (default HEAD: uncommitted work)
- `--project`: affected: Unity project folder (default .)
- `--commit`: commit the run belongs to (default: git rev-parse HEAD)
- `--dir`: where the history lives (default .playcal/tests)
- `--no-record`: check without adding this run to the history

## playcal ui

draw the run history (loop, observe, replay, visual) as one static HTML page, newest first.

- `--history`: history file (default: $PLAYCAL_HISTORY or .playcal/history.jsonl)
- `--out`: where to write the page (default .playcal/ui.html)
- `--last`: only the newest N runs
- `--open`: open the page in the default browser

## playcal visual

screenshot gate: compare a PNG to a saved baseline with a per-pixel tolerance, write a diff PNG on a fail.

- `action`: save: make this PNG the baseline; check: compare a PNG to it
- `name`: baseline name, e.g. level4-start
- `png`: the screenshot (8- or 16-bit PNG)
- `--dir`: where baselines live (default .playcal/visual)
- `--tolerance`: per-channel difference a pixel may have, 0-255 (default 8)
- `--max-changed`: percent of pixels allowed to change (default 0.1)
- `--ignore`: skip a box that changes on its own, like a clock or FPS counter (repeatable)

