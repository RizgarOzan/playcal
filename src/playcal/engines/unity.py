"""Unity through the Unity CLI: every Unity command playcal sends is spelled out here."""
from __future__ import annotations

from typing import Sequence

from .. import loop


class Unity:
    name = "unity"

    def __init__(self, runner=None):
        self.runner = runner or loop.unity_runner

    def ready(self, project: str | None, timeout: float = 120, **settle_kw) -> dict:
        return loop.settle(self.runner, project, timeout, **settle_kw)

    def run(self, argv: Sequence[str], project: str | None) -> dict:
        return loop._call(self.runner, list(argv), project)

    def read(self, reads: Sequence[Sequence[str]], project: str | None) -> list[dict]:
        return loop._reads(self.runner, project, reads)

    def status(self, project: str | None) -> dict:
        return self.run(self.STATUS, project)

    def play(self, project: str | None) -> dict:
        return self.run(["editor.playmode", "start"], project)

    def stop(self, project: str | None) -> dict:
        return self.run(["editor.playmode", "stop"], project)

    def screenshot(self, path: str, project: str | None) -> dict:
        return self.run(["capture", "game_view", "--output-path", path], project)

    def console_since(self, mark: dict) -> list[str]:
        """The read that returns errors logged after a `console status` mark; goes into `read` with others."""
        return ["console", "get", "--level", "error", "--since", str(mark["cursor"]),
                *(["--since-session", mark["session"]] if mark.get("session") else [])]

    STATUS = ["editor", "status"]
    CONSOLE_MARK = ["console", "status"]
