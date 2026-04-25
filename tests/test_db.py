import sqlite3

from jarvis.storage.db import JarvisDB


def test_db_initializes_core_tables(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    with db.conn() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"tasks", "messages", "agent_results", "approval_requests"}.issubset(tables)


def test_db_migrates_old_task_status(tmp_path):
    path = tmp_path / "jarvis.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'todo',
            agent TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        conn.execute(
            "INSERT INTO tasks(title,description,status,agent,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("legacy", "", "todo", "backend", "now", "now"),
        )

    db = JarvisDB(path)

    assert db.get_task(1)[3] == "new"


def test_task_messages_agent_results_and_approvals(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    task_id = db.add_task("Review", "Check safety", "reviewer")
    db.add_message("user", "hello", task_id)
    db.add_agent_result("reviewer", "OK: fine", task_id, "ok")
    approval_id = db.create_approval(123, "shell", {"command": "git status"})

    assert db.has_successful_review(task_id)
    approval = db.get_pending_approval(approval_id, 123)
    assert approval["payload"]["command"] == "git status"
    db.decide_approval(approval_id, "approved")
    assert db.get_pending_approval(approval_id, 123) is None
