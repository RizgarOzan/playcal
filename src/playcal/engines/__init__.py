"""Engine adapters: the six things playcal asks of an editor, so the modules above don't speak one engine's CLI.

  ready       wait until the editor answers and isn't compiling or reloading (loop.settle's verdict)
  run         one editor command, result shaped like {success, exit_code, errors, warnings, data}
  read        several reads at once, each result shaped like run's
  console     console position now, and the errors logged since a position
  screenshot  save the game view to a path
  play / stop enter and leave Play Mode; status says which mode the editor is in

Unity speaks to a live Editor; Godot runs headless one verb at a time (see godot.py). A module takes either an Engine or a bare runner (tests pass fake runners),
and `wrap` turns the runner into the Unity engine.
"""
from __future__ import annotations

from .godot import Godot
from .unity import Unity

ENGINES = {"unity": Unity, "godot": Godot}


def get(name: str = "unity", runner=None):
    if name not in ENGINES:
        raise ValueError(f"unknown engine {name!r}; known: {', '.join(ENGINES)}")
    return ENGINES[name](runner)


def wrap(engine_or_runner):
    return engine_or_runner if hasattr(engine_or_runner, "play") else Unity(engine_or_runner)
