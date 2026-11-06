# playcal

<!-- mcp-name: io.github.RizgarOzan/playcal -->

[![test](https://github.com/RizgarOzan/playcal/actions/workflows/test.yml/badge.svg)](https://github.com/RizgarOzan/playcal/actions/workflows/test.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/playcal.svg)](https://pypi.org/project/playcal/)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
[![good first issues](https://img.shields.io/github/issues/RizgarOzan/playcal/good%20first%20issue?label=good%20first%20issues)](https://github.com/RizgarOzan/playcal/issues?q=is%3Aopen+label%3A%22good+first+issue%22)

**One open-source toolkit for AI agents that build games.** It gives your agent a loop it can trust (edit → wait until the editor has really settled → check the change actually landed → look only at what changed → assert), and it checks bot playtests against real players: *"the bot finds level 12 easy, but people will pass it about 41% of the time (CI 30–53%)."*

Unity first, built on top of Unity's official CLI. Godot works too (headless, through a probe script). Unreal is postponed, see the table below.

*AI agents: read [llms.txt](llms.txt) first.*

> **Status: pre-alpha (v0.1.0).** Today the event schema, `ingest`, `report`, `calibrate`, `logs`, `loop`, `query`, `snap`, `observe`, `replay`, `baseline` (perf and memory), `visual`, `tests`, `affected`, `lock`, `meta`, `checkpoint`, `doctor`, the MCP server and the Unity SDK work; `loop`, `query`, `snap`, `observe`, `replay` and the SDK frame recorder were checked on a live Unity 6000.3 Editor. Everything below marked *planned* isn't built yet. I'd rather show a short honest list than a long fake one. Progress is tracked in the [roadmap](#roadmap).

![A script spawns coins from a null prefab: playcal observe runs the game, reports the exception with file and line and exits 1; after the fix it exits 0](docs/demo.gif)

*A real run on a live Unity 6000.3 Editor: the text is what playcal printed, and the waits are sped up (their real length is shown).*

## Why

Agents working in a game editor fail in boring, repeatable ways:

- they run a tool before compile or domain reload has finished, then reason about stale state;
- the editor isn't focused, so Play Mode sits on frame 1 while reporting "playing";
- a modal dialog blocks everything and nobody notices;
- they report "done" when the prefab was never saved;
- a single console read or hierarchy dump eats 10k+ tokens.

Unity's official CLI and the community MCP servers give you the building blocks: play, test, capture, console. Nothing ties them into a loop that handles these failures. playcal is that loop, plus scene diffs that survive an Editor restart, perf and screenshot gates, flaky-test quarantine, and calibration against human players. See the [benchmark](#benchmark) for what the loop does and doesn't buy you on a live Editor.

## Install

Requires Python 3.10+.

```bash
pipx install playcal
playcal modules
```

For the latest main, use `pipx install git+https://github.com/RizgarOzan/playcal`. The Unity SDK installs from a git URL, see [Unity SDK](#unity-sdk-works-today).

### Install with your AI agent

Paste this into Claude Code, Cursor, Codex or any agent with a shell:

```text
Install playcal for this project:
1. Make sure pipx exists (python -m pip install --user pipx && python -m pipx ensurepath).
2. Run: pipx install playcal
3. Run: playcal modules --json  and show me the module list.
4. Read https://github.com/RizgarOzan/playcal#usage and tell me which commands fit this project.
Do not change project files yet.
```

### MCP server and skill

`playcal mcp` is a stdio MCP server. Every module becomes a tool (`playcal_ingest`, `playcal_logs`, `playcal_loop`, ...), built from the same argument definitions as the CLI, so the two can't drift apart.

The server's `initialize` reply carries a short "which command when" guide as MCP `instructions`, the same lines that open the skill file, so an agent knows to run `doctor` first and to put Editor writes through `loop`.

```bash
# or pick it from the MCP Registry as io.github.RizgarOzan/playcal
claude mcp add --transport stdio playcal -- playcal mcp
code --add-mcp '{"name":"playcal","type":"stdio","command":"playcal","args":["mcp"]}'
```

If your agent has a shell, a skill file is cheaper than MCP (no tool list in every prompt):

```bash
mkdir -p .claude/skills/playcal && playcal skill > .claude/skills/playcal/SKILL.md
```

Or get both at once. In Claude Code, the plugin adds the skill and the MCP server (it starts the server with `pipx run playcal mcp`):

```text
/plugin marketplace add RizgarOzan/playcal
/plugin install playcal@playcal
```

For other agents, [skills.sh](https://skills.sh) installs the same skill file:

```bash
npx skills add RizgarOzan/playcal
```

`playcal skill --project .` writes `.claude/skills/playcal/SKILL.md` into a game project. It spots the engine (Unity: `Assets/` + `ProjectSettings/`, Godot: `project.godot`) and puts the red gate first: the settle and observe commands that must pass before a change counts as done. In a test, a fresh Claude Code session given only "add a print to main.gd" in the Godot sample loaded the skill and ran `loop settle --engine godot` without being asked (it checked the print with plain Godot instead of `observe`).

## Usage

Every command can print `--json`, so agents can read the result without scraping text.

### `playcal ingest` (works today)

This command validates gameplay events against [`schema/event-v0.json`](schema/event-v0.json). It accepts JSONL files, or free text that contains `PC0:` blobs. The in-game "send result" button produces those blobs, and players can paste them into any chat.

```bash
playcal ingest sessions.jsonl --json
playcal ingest whatsapp-export.txt --paste
```

Valid events go to `accepted.jsonl`. Every line that fails goes to `rejected.jsonl` with the reason.

The schema doesn't accept unknown fields, so names, emails, device IDs, location and IP can't slip in. See [PRIVACY.md](PRIVACY.md).

### `playcal report` (works today)

Per-level pass rates, counted two ways: per player and per attempt. Each rate comes with a 95% Wilson interval, because with 10 players a single "60%" means very little.

```bash
playcal report accepted.jsonl
playcal report accepted.jsonl --json
```

Different `game_version`s stay separate, and levels sort naturally (1, 2, 10).

### `playcal calibrate` (works today)

Bots can play every level on every build, but you want to know how people will do. `calibrate` takes the bot runs plus a small human panel that played some of the levels, learns how bot pass rates map to human ones, and predicts the human rate for every level the bots played, each with a 95% interval.

```bash
playcal calibrate --human panel.jsonl --bot bots.jsonl
# version    level      bot      predicted human   human seen
# 1          03         50%        35% [21%-52%]     36% n=25  *
# 1          06         67%        50% [33%-66%]            -
# leave-one-out: 5/5 human rates inside their interval, median error 4 points.
```

Both files use the same event schema. It fits human logits to bot logits per level with a random-effects regression, so levels that the bots get wrong in their own way make every interval wider instead of quietly pulling the line. You need humans on at least 3 levels (5+ attempts each, `--min-attempts`). From 4 levels on it also leaves each one out in turn and checks whether its real rate lands in the predicted interval; that line tells you how far to trust the rest. With a handful of levels the intervals are wide, and that's the honest answer.

### `playcal logs` (works today)

Boils a Unity `Editor.log` (or Player.log, or pasted console text) down to what an agent needs. The same error a thousand times becomes one entry with `count: 1000`, and the first cause is pulled out on its own, since later errors are usually fallout.

```bash
playcal logs --json                         # this machine's Editor.log
playcal logs Editor.log --since "Reloading assemblies"
cat Player.log | playcal logs -
adb logcat -d | playcal logs -             # Android: only the Unity tag is read
```

Exit code 1 means it found errors. In CI it reads a batch-mode build log the same way, so the job fails on the first cause instead of a wall of fallout:

```yaml
- run: unity -batchmode -quit -projectPath . -executeMethod Build.Run -logFile build.log || true
- run: pipx run playcal logs build.log --json
```

### `playcal loop` (works on a live Editor)

`loop settle` waits until the Editor is really ready: two "ready" polls in a row, because a domain reload can flicker. While Unity compiles or reloads, `unity status` shows no Editor at all, so settle asks `unity pipeline list` why: Safe Mode, Unity running but stuck on a dialog (`editor_blocked`), or really no Editor. Two open Editors are reported too, instead of timing out on all of them.

`loop run` checks the Editor is ready, runs one `unity command`, checks again, and can check its own work. Each check is one `editor status` call, which says outright whether the Editor is compiling or reloading; settle's polls are the fallback for a busy or silent Editor and for a write that starts a reload:

```bash
playcal loop run "gameobject create" --verify "scene get_hierarchy" --expect "result.roots.*.name=Probe" --diff --json -- --name Probe
```

`--verify` runs a read command after the write, `--expect` asserts on its JSON (`path=value`, `path!=value`, `path~text`, or a bare `path` for "exists"; `*` matches any item), and `--diff` shows what changed between the read before and after. The digest says `fault: "infra"` when the Editor or CLI broke and `fault: "task"` when the command itself was wrong, so an agent knows whether to retry or rethink. A write that reported success but didn't land fails with `verify_failed` (exit 5).

Two traps we hit on a live Editor (Unity 6000.3, Pipeline 0.10.0-exp.1):

- An Editor with compile errors still says "ready". `--compile` also runs `unity recompile` and fails with `compile_error` (exit 6) and the errors as `file`, `line`, `code`, `message`.
- Some commands start a reload a few seconds *after* they return (`package resolve` says `requiresRecompile`). loop sees that and waits for the reload to start before it trusts "ready" again; `--reload-wait SECONDS` forces the same wait for anything else, like a script written to disk.

Read, write and `--expect` passed against that Editor. The Safe Mode branch itself hasn't been seen live yet.

### `playcal query` (works today)

A full hierarchy dump can be tens of thousands of tokens when the agent needs three names. `query` keeps only what you ask for and says how much it cut:

```bash
playcal query --unity "scene get_hierarchy" --from "result.roots.**" --where "name~Enemy" --select name --select activeSelf --json
playcal query scene.json --from data.objects --where "tag=Enemy" --select name
```

`--where` uses the same syntax as `loop --expect`. `*` is any item of a list, `**` is anything at any depth.

### `playcal checkpoint` (works today)

Saves the project's files before an agent step and brings any save back. Saves are git commits on a private ref (`refs/playcal/checkpoints`), so your branch, HEAD and staging area don't move and nothing is pushed. `.gitignore` is respected.

```bash
playcal checkpoint save "before level 4 tweak"
playcal checkpoint list
playcal checkpoint diff latest
playcal checkpoint restore ~1     # one save back; prints how to undo the restore
```

Restore only rewrites files that differ, so Unity doesn't reimport everything. With `loop run --checkpoint`, every write is preceded by a save and a failed digest carries an `undo` command. Unity only writes scenes and prefabs to disk when they're saved, so unsaved Editor changes aren't in a checkpoint.

### `playcal snap` (works today)

Saves a read under a name and later tells you what changed, so "what did this run do to the scene" is a few lines instead of two dumps:

```bash
playcal snap save before --unity "scene get_hierarchy"
# ... the agent works, or you play a round ...
playcal snap diff before --unity "scene get_hierarchy"     # exit 1 when something changed
playcal snap diff run-a run-b --json
```

Unity's instanceIds only hold within one Editor session, so a plain diff across a restart or into play mode says everything was removed and re-added. snap leaves `instanceId` out and matches objects by `hierarchyPath`, then `path`, `guid`, `name`; two siblings with the same name become `Enemy` and `Enemy#2`. `isDirty` is left out too, since it flips on every edit. Any JSON file works, not only Unity reads. Saves go to `.playcal/snaps/`.

On a live Editor we saved a scene, added an object, and the diff said `added: result.roots[hierarchyPath=/SnapProbe]`. Then we saved, restarted Unity and diffed again: no changes, even though every instanceId was new and Unity had handed one object's old id to another object.

### `playcal observe` (works on a live Editor)

Presses Play, lets the game run a few seconds, and says what happened in one digest: the console errors logged during the run (collapsed and with the first cause, like `logs`), what changed in the scene (with `snap`'s rules), and any `--expect` checks on the scene while it plays. Only a red run takes a screenshot.

```bash
playcal observe --seconds 5 --expect "result.roots.*.name=Player"
# errors (task)
#   LogError: boss has no target  at Assets/Boss.cs:13
#   scene: 1 added, 0 removed, 0 changed
#   played: 5.0-19.4 s before the second read
#   shot: C:\MyGame\.playcal\observe\observe.png
```

Exit 0 is clean, 1 is red (console errors, a failed check, a read that failed, Play Mode that never started), and 3 or 4 is the Editor itself (same codes as `loop`). Exit 3 with reason `console_unread` means the console couldn't be read, or was cleared or overflowed during the run, so errors could have slipped past; observe won't call that clean. The console mark is taken before Play starts, so errors from earlier don't count. The scene diff is only reported, since a running game is supposed to change; gate on it with `--expect`. `--read CMD` diffs another read instead of `scene get_hierarchy`, `--no-play` diffs two reads `--seconds` apart without touching Play Mode, and an Editor that's already playing or paused is watched and left as it was. Play Mode that observe started is always stopped again, even when the run fails halfway. `played` is how long the game ran before the second read: at least the first number (counted from when the Editor said it was playing), at most the second (counted from the start request).

On a live Editor (Unity 6000.3, Pipeline 0.10.0-exp.1) a clean `observe --seconds 3` takes about 33 s. The reload isn't what costs: `editor.playmode start` only comes back once it's done. What costs is the CLI itself, 3–5 s for every `unity` call, whatever it does. So observe makes 7 calls where it used to make 16: the reads before and after Play go through one `job batch` call each, and it asks `editor status`, which says "playing" right away, instead of polling `unity status`, which can list no Editor for several seconds after that. The first version took about 70 s and let the game run ~26 s when asked for 3. Then I added a script that spawns from a missing prefab: observe reported the `ArgumentException` with its file and line, `1 added` in the scene, and saved a real Game view screenshot, in 45 s. Not seen live yet: a project with "Enter Play Mode Options" set to skip the reload, and a console that overflows during the run (the digest says `dropped` when it does).

### `playcal playtest` (works on a live Editor or Godot)

One green run doesn't tell you a bug is gone; a flaky null ref passes one run in three. `playtest` runs observe several times (default 3) and prints a checklist: each `--expect` row and "no console errors", with pass, fail and skip counts. Every run red is `red` (exit 1), some red is `inconclusive` (exit 7), all green is `clean`. Runs that broke for infra reasons (no Editor) count as skip. `--events bot.jsonl --level 4` writes each run as one PC0 attempt, so `playcal calibrate --bot bot.jsonl` and `playcal report` read it. `playcal observe --repeat N` does the same thing.

```bash
playcal playtest --engine godot --project-path . --expect "Player/hp=3" --events .playcal/bot.jsonl --level 4
```

### `playcal audio check` (offline, Unity or Godot)

A streamed UI click starts late and a 3-minute track set to Decompress On Load eats memory. `audio check` reads each clip's import settings (`.meta` in Unity, `.import` in Godot) and checks them against a folder policy, first matching rule wins. WAV files are also measured with the stdlib only: integrated loudness in LUFS (BS.1770), true peak in dBTP and clipped samples. Any problem is exit 1 with the file and the rule. The default policy keeps `*/UI/*` and `*/SFX/*` in memory (UI also mono), streams `*/Music/*` and wants peaks under -1 dBTP; `--policy FILE` replaces it and can add a `"lufs": [lo, hi]` band per rule.

```
playcal audio check --project-path .
# Assets/UI/click.wav: load type Streaming, */UI/* wants DecompressOnLoad (short UI sounds should sit in memory; a streamed click starts late)
```

### `playcal replay` (works on a live Editor)

Record a session's input once, then play it back after every change and see whether the game still does the same thing. The game reads input through the SDK and marks what should come out the same:

```csharp
float move = PlaycalInput.Axis("move.x", Input.GetAxis("Horizontal"));  // live value, or the recorded one during a replay
if (PlaycalInput.Button("jump", Input.GetButton("Jump"))) Jump();
PlaycalReplay.Mark("score", score);                                     // compared frame by frame
```

```bash
playcal replay record jump-test --frames 600     # press Play, record 600 frames, leave Play
playcal replay run jump-test                      # replay with the same frame rate and seed, compare the marks
# diverged
#   frame 150: expected x=0.181579992 at 150, got x=0.150087655 at 150
playcal replay run jump-test --stop-at 120        # pause on frame 120 and stay in Play Mode
playcal replay step --frames 5                    # advance the paused Editor 5 frames
playcal replay check jump-test                    # compare the last result again, e.g. with --tolerance
```

The SDK picks the request up from `.playcal/replays/request` when Play starts, so nothing in the scene has to change. While recording or replaying it sets `Time.captureFramerate` to the recording's fps, so `deltaTime` is the same every frame, and seeds `UnityEngine.Random` (a `System.Random` you create yourself isn't touched). Exit 0 is a match, 1 is a divergence, a replay that ended early, console errors during the run, or `no_marks` (nothing was marked, so nothing was compared); 3 is `console_unread`, as in observe. `no_sdk` means the request was never read. Whole-number marks must match exactly; `--tolerance` only loosens decimal ones. Read input in `Update`, not `FixedUpdate`, since frames are what's recorded.

On a live Editor (Unity 6000.3, Pipeline 0.10.0-exp.1) I recorded 300 frames from a probe script whose "live" input came from the wall clock, so only the recording could reproduce it. Its marks were a moved value, `Random.value` and the position of a Rigidbody ball rolling on a box. Two replays matched the recording exactly, even with `--tolerance 0`, physics included. With one input changed at frame 150, the run said `diverged` at frame 150. `--stop-at 120` left the Editor paused on replay frame 120, and `step --frames 5` moved it to 125. With the same changes as observe, a 120-frame record takes about 42 s and a run about 37 s, most of it the 3–5 s each `unity` call costs. Not checked yet: a person playing on a keyboard, replays across an Editor restart or on another machine, a player build, scenes with many bodies or joints, and the new Input System (route its values through `PlaycalInput` the same way). Since `deltaTime` is fixed, a machine that can't keep up runs the recording in slow motion instead of dropping frames.

### `playcal baseline` (works today on JSON)

Keeps a perf run as the baseline and fails a later run only when it is slower by more than noise. Feed it per-frame samples, `{"frame_ms": [...], "gc_alloc_bytes": [...]}` or a bare list of frame times:

```bash
playcal baseline save level4 run-main.json
playcal baseline check level4 run-branch.json                 # exit 1 only when slower
playcal baseline check level4 run.json --percentile 99 --tolerance 5 --floor frame_ms=0.5
```

It compares p95 by default and bootstraps the difference. The check fails only when even the low end of the 95% interval is past the tolerance (10% by default). A short run with a couple of hitches comes out `inconclusive`, so one noisy CI machine doesn't turn the build red. `--floor` ignores slowdowns that are too small to matter in absolute units. Baselines live in `.playcal/baselines/`. The Unity SDK's `PlaycalFrames.Begin("level4")` records the samples, and `Save()` writes them where `baseline` can read them. Its `used_memory_bytes` series goes through the same check, so a run that needs more memory fails the same way; add `--floor used_memory_bytes=1048576` to ignore growth under 1 MB. The visual gate is `playcal visual`, below.

### `playcal tests` (works today)

Keeps a history of test results and tells a flaky failure from a real one. It reads what Unity's `-testResults` writes (NUnit3 XML) and JUnit XML:

```bash
playcal tests check TestResults.xml        # exit 1 only on failures of tests that aren't flaky
playcal tests flaky                        # tests that passed and failed on the same commit
```

A test counts as flaky once it has both passed and failed on one commit, so the first rerun that passes is what marks it. After that its failures show up as `quarantined` instead of turning the run red. They're still listed, so nobody forgets them. `check` judges the run against the history first and then adds it (`--no-record` skips that). The history is `.playcal/tests/history.jsonl`; keep it in CI's cache or commit it. A flaky test stays quarantined on later commits too, until you fix it and drop its lines from the history.

`affected` says which test assemblies a change can break, so a small change doesn't have to wait for the whole suite:

```bash
playcal tests affected                     # uncommitted work; --base main for a branch
# -runTests -assemblyNames "Game.UI.Tests"
```

It reads the project's `.asmdef` and `.asmref` files (names and `GUID:` references), puts each changed script in the assembly of the nearest asmdef above it, and follows references outward to the test assemblies. Scripts with no asmdef go to `Assembly-CSharp` or `Assembly-CSharp-Editor`. A scene, prefab, setting or `Packages/manifest.json` change can't be traced through code, so it says so and asks for a full run. Packages referenced with `file:` from outside the project aren't scanned.

### `playcal visual` (works today on PNG files)

A screenshot gate. Save a shot as the baseline, then check later shots against it:

```bash
playcal visual save level4-start shot.png
playcal visual check level4-start shot.png     # exit 1 if more than 0.1% of pixels changed
# changed: 4096 pixels (0.1975%) differ, box [900, 300, 963, 363], see .playcal/visual/level4-start.diff.png
```

A pixel counts as changed when one of its channels moves by more than `--tolerance` (default 8 out of 255), so antialiasing and compression noise pass while a missing sprite doesn't. `--max-changed` sets how many percent of pixels may change. On a fail it writes a diff PNG with the changed pixels in red over a pale copy of the new shot, and the box tells an agent where to look. It reads 8- and 16-bit PNGs (grey, RGB, RGBA, no interlace) with the standard library only, and compares 16-bit shots at 8 bits (the high byte of each sample); a 1920x1080 shot takes about 2 s. Take shots at a fixed resolution, with animations paused, or the gate measures the animation. A part that changes on its own, like a clock or an FPS counter, can be skipped with `--ignore x0,y0,x1,y1` (repeat it for more boxes).

### `playcal ui` (works today)

Every `loop`, `observe`, `replay` and `visual check` run adds one line to `.playcal/history.jsonl`. `playcal ui` draws that file as one static HTML page, newest run first:

```bash
playcal ui --open            # writes .playcal/ui.html and opens it
playcal ui --last 20         # only the newest 20 runs
```

Each row shows what ran, the verdict, compile and console errors as `file:line`, failed checks, the scene diff, the checkpoint to undo to, and the screenshot or visual diff image. The page has no server and no scripts. Set `PLAYCAL_HISTORY` to write the history somewhere else, or to `off` to write nothing.

### Godot (works today, headless)

No plugin goes into the project. playcal runs `godot --headless` with its own probe script, so each call is one short run. Put Godot on PATH or set `PLAYCAL_GODOT` to the binary.

```bash
playcal loop settle --engine godot --project-path my_game   # loads every .gd; a parse error is compile_error (exit 6) with file:line
playcal observe --engine godot --project-path my_game        # runs the main scene 3 s; push_error and script errors come back as file:line, exit 1
playcal snap save before --godot --project-path my_game      # node tree of the main scene, keyed by node path
```

There is no live editor to send commands to, so `loop run` is Unity only: edit the files, then settle. Headless runs draw nothing, so observe takes no screenshot on Godot. Checked on Godot 4.7.2 on Windows.

### `playcal meta` (works today)

An agent that moves `Player.cs` with `mv` leaves `Player.cs.meta` behind. Unity gives the script a new GUID on the next import and every scene that used it shows "Missing script". `meta` reads the project files, no Editor needed, and catches that before the import:

```bash
playcal meta --project .     # exit 1 on errors; --json for the full list
```

Errors are things that will break: two `.meta` files with one GUID, an asset moved without its `.meta`, a `.meta` with no GUID, and a scene or prefab pointing at a script GUID nothing has. Warnings are new files without a `.meta` yet, leftover `.meta` files, case mismatches, and MonoBehaviour classes whose name isn't the file name. Script GUIDs from packages are read from `Library/PackageCache`; on a fresh clone without it, missing scripts are only warnings.

### `playcal lock` (works today)

When two agents, or an agent and a person, work on one project, they shouldn't both edit `Level4.unity`. `lock` takes a lock file under `.playcal/locks/`:

```bash
playcal lock take Assets/Scenes/Level4.unity --owner agent-a --ttl 30m   # exit 1 and who holds it, if taken
playcal lock check Assets/Scenes/Level4.unity --owner agent-b
playcal lock release Assets/Scenes/Level4.unity --owner agent-a
playcal lock list
```

Taking several paths is all or nothing. Locks expire, so a crashed agent can't hold a scene forever, and taking your own lock again renews it. `PLAYCAL_OWNER` saves typing `--owner`. A lock only works if every agent asks before it writes; it doesn't stop Unity or git from writing the file. If you use Git Bash, pass Unity paths without a leading `/` (or set `MSYS_NO_PATHCONV=1`), since Git Bash rewrites `/SnapProbe` into a Windows path.

### `playcal doctor` (works today)

Run it first when something won't start. It checks Python, git, the Unity CLI, each Editor the CLI can see (running, stuck on a dialog, in Safe Mode) and, with `--project`, whether the SDK is in `Packages/manifest.json`. It exits 1 if any check fails; `--json` prints the checks as a list.

```bash
playcal doctor --project C:/Games/MyGame
# ok    unity cli: 1.0.0-beta.12
# fail  editor C:\Games\MyGame: running but not answering; usually a modal dialog is open in the Editor
```

### Unity SDK (works today)

Install from git URL `https://github.com/RizgarOzan/playcal.git?path=/unity/com.playcal.sdk`, then call `PlaycalLog.Init()`, `log.LevelStart("04")`, `log.LevelFail(...)`. The "send result" button calls `PlaycalLog.CopyResult()`. Usage is in the [package README](unity/com.playcal.sdk/README.md). A test checks that the C# output passes the same schema as the Python side.

For perf runs, `PlaycalFrames.Begin("level4")` records frame time, GC bytes allocated per frame and total used memory (through `ProfilerRecorder`) and skips the first 30 frames. Call `Save()` at the end and pass the file to `playcal baseline`. GC numbers measured in the Editor include the Editor's own allocations (about 15 KB per frame in our test scene), so take baselines from a player build.

For `playcal replay`, read input through `PlaycalInput.Axis` / `PlaycalInput.Button` and note results with `PlaycalReplay.Mark`; the [replay section](#playcal-replay-works-on-a-live-editor) has the details.

For the visual gate, `PlaycalShots.Capture("level4-start")` saves a screenshot to `persistentDataPath/playcal/shots/`. In a batch-mode test run it renders `Camera.main` instead, since the screen capture never fires there.

## Roadmap

| Module | What it does | State |
|---|---|---|
| `ingest` | event schema, validation, paste blobs | ✅ works |
| `report` | per-level pass rate, attempts, Wilson CI | ✅ works |
| Unity SDK | level events, PC0 "send result" line, delete-my-data, under-18 off | ✅ works |
| `loop` | edit → settle (compile/reload, focus, modal) → verify write → diff → assert, one JSON digest | ✅ read, write and `--expect` checked on a live Unity 6000.3 Editor; Safe Mode branch not seen live |
| `calibrate` / `predict` | bot metrics + small human panel → predicted human pass rate with CI | ✅ works on attempt pass rates; not yet checked against a real panel |
| `query` | pick, filter and project fields from any JSON result (file, stdin or a Unity read command) | ✅ works on JSON and on live `scene get_hierarchy` reads |
| `checkpoint` | every agent step is a git-backed, revertible checkpoint | ✅ works (`loop run --checkpoint` saves before each write) |
| `logs` | console/Editor.log summary: dedupe, first cause, error kinds | ✅ works |
| `mcp`, `skill` | MCP server and SKILL.md generated from the module registry | ✅ works |
| `snap` | save a read, diff two saves with session ids left out | ✅ works, checked across an Editor restart |
| `observe` | whole-scene runtime diff, screenshots only on red | ✅ works, checked on a live Unity 6000.3 Editor (errors, scene diff, screenshot) |
| `baseline` | visual, perf (p95 frame time, GC) and memory gates per commit | ✅ perf and memory gates, the SDK recorder, and the visual gate as `playcal visual` |
| `replay` | frame stepping, recorded input, replay → regression test | ✅ works, checked on a live Unity 6000.3 Editor (record, match, divergence, stop and step); not yet with a person playing or across restarts |
| `tests` | flaky history and quarantine, affected tests | ✅ history, flaky detection, quarantine (NUnit3, JUnit) and affected test assemblies from the .asmdef graph |
| `lock` | scene/asset locks for several agents at once | ✅ works (advisory lock files with expiry) |
| `meta` | .meta/GUID damage from files moved outside the Editor | ✅ works (offline, reads files only) |
| `doctor` | check the setup before a run | ✅ works |
| `audio` | audio import settings against a folder policy, WAV loudness and peaks | ✅ works (offline, Unity `.meta` and Godot `.import`) |
| Godot | same core, new backend | ✅ `loop settle`, `observe`, `snap`, checked on Godot 4.7.2 |
| Unreal | same core, through Epic's editor MCP server (127.0.0.1:8000/mcp) | postponed: I don't have an Unreal install to check it against, and I won't ship an adapter I haven't run. A PR from someone who has one is welcome |

Every row is one module. A module is a Python entry point in the `playcal.modules` group, with optional Editor C#, its own tests and its own benchmark scenario. New features arrive as new modules, not as new tools to install.

## Benchmark

[`bench/live.py`](bench/live.py) runs one write against a real, open Editor in four states, two ways: a naive agent that sends the plain `unity command` and trusts `success`, and `playcal loop run` with a read-back check. "Landed" is read back from the Editor afterwards, not taken from either side's word. Unity 6000.3.9f1, Pipeline 0.10.0-exp.1, Windows 11, 3 rounds:

| Editor state | naive landed | playcal landed | false successes (either) | median time naive / playcal |
|---|---|---|---|---|
| idle | 3/3 | 3/3 | 0 | 2.2 s / 7.4 s |
| mid domain reload | **0/3** | **3/3** | 0 | 2.2 s / 33.6 s (19–41 s, the reload itself) |
| in Play Mode (write refused) | 0/3 | 0/3 | 0 | 3.9 s / 28.0 s |
| bad value (string into a float) | 0/3 | 0/3 | 0 | 4.3 s / 5.4 s |

What this says, plainly:

- A write sent while Unity reloads is dropped by the plain CLI and lands through playcal. The reload window was really hit in 6 of 6 runs.
- Unity's CLI is stricter than I expected. It refused Play Mode writes and bad values loudly, so neither side reported a single false success here. playcal's digest does add which kind of failure it was (`fault: task` for both of those), so an agent knows not to just retry.
- playcal is still about 3x slower than a bare call on a quiet Editor. Every `unity` call costs about 3 s, and part of that is the CLI sending usage analytics on `status`. Each ready check is one `editor status` call, with the read-back in the same batch, so a quiet write is 3 calls instead of the 6 it started at: the idle case went from 24 s to 15 s to 7 s ([#4](https://github.com/RizgarOzan/playcal/issues/4)). The mid-reload time is mostly the reload itself; it took 10 to 24 s on this run before the Pipeline server even went away, and 14 s on a quieter one.
- The refused and bad-value cases take as long as the Editor takes to say no. Play Mode held the write for about 25 s before refusing it; the bad value came back in 5 s.
- Reading the names of a few objects: the full `scene get_hierarchy` was 5216 bytes, `playcal query` 682. The test scene is tiny; the gap grows with the scene.

Not measured yet: an MCP server as a third arm, agent token use over a whole task, and the unfocused-Editor and modal-dialog cases on a live Editor. [`bench/sim.py`](bench/sim.py) replays those against a scripted Editor in CI, plus a write that lands but makes the game throw once Play starts (`loop` alone calls that a success; `observe` catches it). It's a regression guard, not evidence about Unity.

Run it on your own project (it creates and deletes `BenchLive*` objects and one temporary script):

```bash
python bench/live.py --project-path C:/path/to/Project --repeat 3
```

Predicting human difficulty with agents isn't a new idea. Roohi et al. ([2021](https://arxiv.org/abs/2107.12061)) did it for a Rovio game with a large player base. What playcal adds is doing it with a small panel (10–30 friends), honest confidence intervals, and open code.

## Contributing

The most useful thing you can send is a real case where an agent and a Unity Editor got out of sync: what the agent ran, what it said, what was actually true. There's an issue template for it. After that: new modules, and a Godot or Unreal backend if you know those editors' APIs.

No time for a PR? Running four commands against your own project and posting what they printed in [Testers wanted](https://github.com/RizgarOzan/playcal/discussions/8) tells me more than another test I write myself.

[CONTRIBUTING.md](CONTRIBUTING.md) has the setup (`pip install -e ".[dev]"` and `pytest`, no Unity needed for most of it) and the few ground rules. Please also read the [code of conduct](CODE_OF_CONDUCT.md). Security problems go through [SECURITY.md](SECURITY.md), not public issues.

Small, well-described tasks are under [`good first issue`](https://github.com/RizgarOzan/playcal/labels/good%20first%20issue). If playcal saved you a debugging session, a star helps other Unity people find it.

## License

[Apache-2.0](LICENSE). Use it in commercial games. The patent grant is there so studios don't have to ask.
