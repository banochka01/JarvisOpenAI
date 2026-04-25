from pathlib import Path

import pytest

from jarvis.tools import file_tool


def test_safe_path_allows_workspace_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(file_tool, "WORKSPACE", workspace)

    assert file_tool.safe_path("nested/file.txt") == (workspace / "nested" / "file.txt").resolve()


def test_safe_path_blocks_parent_traversal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(file_tool, "WORKSPACE", workspace)

    with pytest.raises(ValueError):
        file_tool.safe_path("../secret.txt")


def test_safe_path_blocks_absolute_path_outside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "workspace_evil" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(file_tool, "WORKSPACE", workspace)

    with pytest.raises(ValueError):
        file_tool.safe_path(str(outside))


def test_preview_diff_shows_changes(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(file_tool, "WORKSPACE", workspace)

    diff = file_tool.preview_diff("a.txt", "new\n")

    assert "-old" in diff
    assert "+new" in diff
