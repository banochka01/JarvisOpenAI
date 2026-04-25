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


def test_orchestrator_exposes_clarification_questions(tmp_path, monkeypatch):
    import jarvis.orchestrator as orchestrator_module

    db = JarvisDB(tmp_path / "jarvis.db")
    monkeypatch.setattr(orchestrator_module, "build_action_plan", lambda _text, _memory: """{
      "needs_clarification": true,
      "questions": ["Как называется проект?"],
      "summary": "Нужно уточнить",
      "plan": ["дождаться ответа"],
      "proposed_actions": []
    }""")

    result = Orchestrator(db).plan_task("make landing", user_id=123)

    assert result.needs_clarification
    assert result.questions == ["Как называется проект?"]
    assert result.approvals == []


def test_orchestrator_build_task_creates_write_file_approvals(tmp_path, monkeypatch):
    import jarvis.action_protocol as protocol
    import jarvis.orchestrator as orchestrator_module

    db = JarvisDB(tmp_path / "jarvis.db")
    monkeypatch.setattr(protocol, "preview_diff", lambda path, content: f"preview {path}")
    monkeypatch.setattr(orchestrator_module, "build_site_action_plan", lambda _text, _memory: """{
      "summary": "Create static site",
      "plan": ["write html", "write css"],
      "proposed_actions": [
        {"type": "write_file", "path": "site/index.html", "content": "<html></html>"},
        {"type": "write_file", "path": "site/styles.css", "content": "body { color: #111; }"}
      ]
    }""")

    result = Orchestrator(db).build_task("make landing", user_id=123)

    assert result.task_id == 1
    assert result.approvals == [1, 2]
    first = db.get_pending_approval(1, 123)
    second = db.get_pending_approval(2, 123)
    assert first["action_type"] == "write_file"
    assert first["payload"]["path"] == "site/index.html"
    assert second["payload"]["path"] == "site/styles.css"
