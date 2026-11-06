import pytest


@pytest.fixture(autouse=True)
def _history(tmp_path, monkeypatch):
    """Keep module runs in tests from writing .playcal/history.jsonl into the repo."""
    monkeypatch.setenv("PLAYCAL_HISTORY", str(tmp_path / "history.jsonl"))
