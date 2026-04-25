from jarvis.action_protocol import parse_supervisor_plan
from jarvis.tools import file_tool


def test_parse_supervisor_plan_extracts_shell_action():
    text = """{
      "needs_clarification": false,
      "questions": [],
      "summary": "Check repo",
      "plan": ["inspect"],
      "proposed_actions": [{"type": "shell", "command": "git status"}]
    }"""

    plan = parse_supervisor_plan(text)

    assert not plan.needs_clarification
    assert plan.summary == "Check repo"
    assert plan.proposed_actions[0].action_type == "shell"
    assert plan.proposed_actions[0].risk == "read_only"


def test_parse_supervisor_plan_rejects_bad_shell_action():
    text = """{
      "summary": "bad",
      "proposed_actions": [{"type": "shell", "command": "git status & del /s *"}]
    }"""

    plan = parse_supervisor_plan(text)

    assert plan.proposed_actions[0].action_type == "rejected"
    assert "shell action rejected" in plan.proposed_actions[0].preview


def test_parse_supervisor_plan_builds_write_preview(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(file_tool, "WORKSPACE", workspace)
    import jarvis.action_protocol as protocol

    monkeypatch.setattr(protocol, "preview_diff", file_tool.preview_diff)
    text = """{
      "summary": "write",
      "proposed_actions": [{"type": "write_file", "path": "a.txt", "content": "hello"}]
    }"""

    plan = parse_supervisor_plan(text)

    assert plan.proposed_actions[0].action_type == "write_file"
    assert "a/a.txt" in plan.proposed_actions[0].preview
