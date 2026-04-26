import sqlite3

from jarvis.storage.db import JarvisDB


def test_db_initializes_core_tables(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    with db.conn() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert {"tasks", "messages", "agent_results", "approval_requests", "steam_games", "pc_shortcuts"}.issubset(tables)


def test_db_seeds_steam_games(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    games = dict(db.list_steam_games())

    assert games["548430"] == "Deep Rock Galactic"
    assert games["2707940"] == "FPV Kamikaze Drone"
    assert games["1222700"] == "A Way Out"
    assert games["322170"] == "Geometry Dash"
    assert games["1217060"] == "Gunfire Reborn"
    assert games["1432320"] == "Liftoff: Micro Drones"
    assert games["2780980"] == "LOCKDOWN Protocol"
    assert games["578080"] == "PUBG: BATTLEGROUNDS"
    assert games["3241660"] == "R.E.P.O."


def test_add_and_get_steam_game_normalizes_app_id(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    db.add_steam_game("Counter-Strike 2", "app/730")

    assert db.get_steam_game("730") == ("730", "Counter-Strike 2")


def test_db_seeds_pc_shortcuts(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    shortcuts = {item["slug"]: item for item in db.list_pc_shortcuts()}

    assert shortcuts["paradeevich-youtube"]["url"] == "https://www.youtube.com/@paradeevich"
    assert "парадеевича" in shortcuts["paradeevich-youtube"]["aliases"]
    assert shortcuts["youtube"]["url"] == "https://www.youtube.com/"


def test_add_and_get_pc_shortcut(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    db.add_pc_shortcut("docs", "Docs", "https://docs.example.test", ["доки"], "site")

    item = db.get_pc_shortcut("docs")
    assert item["name"] == "Docs"
    assert item["aliases"] == ["доки"]


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


def test_list_pending_approvals_for_user_filters_status_and_user(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    first = db.create_approval(123, "shell", {"command": "git status"})
    db.create_approval(456, "shell", {"command": "git diff"})
    done = db.create_approval(123, "steam", {"app_id": "730"})
    db.decide_approval(done, "cancelled")

    rows = db.list_pending_approvals_for_user(123)

    assert [item["id"] for item in rows] == [first]
    assert rows[0]["action_type"] == "shell"
    assert rows[0]["payload"]["command"] == "git status"


def test_pending_clarification_lifecycle(tmp_path):
    db = JarvisDB(tmp_path / "jarvis.db")

    first_id = db.create_clarification_request(123, "make landing", ["What brand?"], mode="plan")
    second_id = db.create_clarification_request(123, "make site", ["Which style?"], mode="build")

    pending = db.get_pending_clarification(123)

    assert pending["id"] == second_id
    assert pending["mode"] == "build"
    assert pending["task_text"] == "make site"
    assert pending["questions"] == ["Which style?"]
    assert first_id != second_id

    db.resolve_clarification(second_id)

    assert db.get_pending_clarification(123) is None
