from jarvis.orchestrator import Orchestrator
from jarvis.storage.db import JarvisDB


def test_orchestrator_creates_task_and_approval(tmp_path, monkeypatch):
    import jarvis.orchestrator as orchestrator_module

    db = JarvisDB(tmp_path / "jarvis.db")
    monkeypatch.setattr(orchestrator_module, "build_action_plan", lambda _text, _memory: """{
      "summary": "Check state",
      "plan": ["inspect"],
      "proposed_actions": [{"type": "shell", "command": "git status"}]
    }""")

    result = Orchestrator(db).plan_task("check project", user_id=123)

    assert result.task_id == 1
    assert result.approvals == [1]
    approval = db.get_pending_approval(1, 123)
    assert approval["action_type"] == "shell"
    assert approval["payload"]["task_id"] == 1


def test_orchestrator_rejected_action_sets_needs_fix(tmp_path, monkeypatch):
    import jarvis.orchestrator as orchestrator_module

    db = JarvisDB(tmp_path / "jarvis.db")
    monkeypatch.setattr(orchestrator_module, "build_action_plan", lambda _text, _memory: """{
      "summary": "Bad action",
      "proposed_actions": [{"type": "shell", "command": "git status & del /s *"}]
    }""")

    result = Orchestrator(db).plan_task("bad task", user_id=123)

    assert result.approvals == []
    assert len(result.rejected_actions) == 1
    assert db.get_task(result.task_id)[3] == "needs_fix"
