import json
import sqlite3
from datetime import datetime
from pathlib import Path


TASK_STATUSES = {"new", "planned", "in_progress", "testing", "needs_fix", "done", "failed"}
STATUS_MIGRATION = {
    "todo": "new",
    "doing": "in_progress",
    "review": "testing",
    "blocked": "needs_fix",
}


class JarvisDB:
    def __init__(self, path: Path):
        self.path = path
        self.init()

    def conn(self):
        return sqlite3.connect(self.path)

    def init(self):
        with self.conn() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )""")
            if db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
                db.execute("INSERT INTO schema_version(version) VALUES(1)")

            db.execute("""CREATE TABLE IF NOT EXISTS memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                agent TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS agent_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                agent TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                created_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS clarification_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                mode TEXT NOT NULL DEFAULT 'plan',
                task_text TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )""")
            clarification_columns = {
                row[1] for row in db.execute("PRAGMA table_info(clarification_requests)").fetchall()
            }
            if "mode" not in clarification_columns:
                db.execute("ALTER TABLE clarification_requests ADD COLUMN mode TEXT NOT NULL DEFAULT 'plan'")
            for old, new in STATUS_MIGRATION.items():
                db.execute("UPDATE tasks SET status=? WHERE status=?", (new, old))
            db.execute("UPDATE schema_version SET version=2")

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def remember(self, key: str, value: str):
        with self.conn() as db:
            db.execute(
                "REPLACE INTO memory(key,value,updated_at) VALUES(?,?,?)",
                (key, value, self._now()),
            )

    def memories(self) -> str:
        with self.conn() as db:
            rows = db.execute("SELECT key,value FROM memory ORDER BY updated_at DESC LIMIT 30").fetchall()
        return "\n".join(f"- {k}: {v}" for k, v in rows) or "Память пока пустая."

    def add_task(self, title: str, description: str = "", agent: str = "", status: str = "new") -> int:
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {status}")
        now = self._now()
        with self.conn() as db:
            cur = db.execute(
                "INSERT INTO tasks(title,description,status,agent,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (title, description, status, agent, now, now),
            )
            return int(cur.lastrowid)

    def get_task(self, task_id: int):
        with self.conn() as db:
            return db.execute(
                "SELECT id,title,description,status,agent FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()

    def list_task_rows(self, limit: int = 100):
        with self.conn() as db:
            return db.execute(
                """SELECT id,title,description,status,agent,created_at,updated_at
                   FROM tasks ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()

    def set_task_status(self, task_id: int, status: str):
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {status}")
        with self.conn() as db:
            db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, self._now(), task_id))

    def list_tasks(self) -> str:
        with self.conn() as db:
            rows = db.execute("SELECT id,title,status,agent FROM tasks ORDER BY id DESC LIMIT 20").fetchall()
        if not rows:
            return "Задач пока нет."
        marks = {
            "new": "⬜",
            "planned": "🧠",
            "in_progress": "▶️",
            "testing": "🧪",
            "needs_fix": "🛠",
            "done": "✅",
            "failed": "⛔",
        }
        return "\n".join(f'{marks.get(s, "•")} #{i} {s} [{a or "agent?"}] {t}' for i, t, s, a in rows)

    def log(self, kind: str, content: str):
        with self.conn() as db:
            db.execute("INSERT INTO logs(kind,content,created_at) VALUES(?,?,?)", (kind, content, self._now()))

    def list_logs(self, limit: int = 100):
        with self.conn() as db:
            return db.execute(
                "SELECT id,kind,content,created_at FROM logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def add_message(self, role: str, content: str, task_id: int | None = None):
        with self.conn() as db:
            db.execute(
                "INSERT INTO messages(task_id,role,content,created_at) VALUES(?,?,?,?)",
                (task_id, role, content, self._now()),
            )

    def add_agent_result(self, agent: str, content: str, task_id: int | None = None, status: str = "ok"):
        with self.conn() as db:
            db.execute(
                "INSERT INTO agent_results(task_id,agent,content,status,created_at) VALUES(?,?,?,?,?)",
                (task_id, agent, content, status, self._now()),
            )

    def has_successful_review(self, task_id: int) -> bool:
        with self.conn() as db:
            row = db.execute(
                """SELECT 1 FROM agent_results
                   WHERE task_id=? AND agent IN ('reviewer','tester') AND status='ok'
                   ORDER BY id DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
        return bool(row)

    def create_approval(self, user_id: int, action_type: str, payload: dict) -> int:
        with self.conn() as db:
            cur = db.execute(
                "INSERT INTO approval_requests(user_id,action_type,payload_json,status,created_at) VALUES(?,?,?,?,?)",
                (user_id, action_type, json.dumps(payload, ensure_ascii=False), "pending", self._now()),
            )
            return int(cur.lastrowid)

    def list_approvals(self, status: str | None = None, limit: int = 100):
        query = """SELECT id,user_id,action_type,payload_json,status,created_at,decided_at
                   FROM approval_requests"""
        params: tuple = ()
        if status:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY id DESC LIMIT ?"
        params = (*params, limit)
        with self.conn() as db:
            return db.execute(query, params).fetchall()

    def get_approval_any_user(self, approval_id: int):
        with self.conn() as db:
            row = db.execute(
                """SELECT id,user_id,action_type,payload_json,status,created_at,decided_at
                   FROM approval_requests WHERE id=?""",
                (approval_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "action_type": row[2],
            "payload": json.loads(row[3]),
            "status": row[4],
            "created_at": row[5],
            "decided_at": row[6],
        }

    def get_pending_approval(self, approval_id: int, user_id: int):
        with self.conn() as db:
            row = db.execute(
                """SELECT id,action_type,payload_json FROM approval_requests
                   WHERE id=? AND user_id=? AND status='pending'""",
                (approval_id, user_id),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "action_type": row[1], "payload": json.loads(row[2])}

    def decide_approval(self, approval_id: int, status: str):
        if status not in {"approved", "cancelled"}:
            raise ValueError("approval status must be approved or cancelled")
        with self.conn() as db:
            db.execute(
                "UPDATE approval_requests SET status=?, decided_at=? WHERE id=? AND status='pending'",
                (status, self._now(), approval_id),
            )

    def create_clarification_request(
        self,
        user_id: int,
        task_text: str,
        questions: list[str],
        mode: str = "plan",
    ) -> int:
        if mode not in {"plan", "run", "build"}:
            raise ValueError("clarification mode must be plan, run or build")
        now = self._now()
        with self.conn() as db:
            db.execute(
                "UPDATE clarification_requests SET status='cancelled', resolved_at=? WHERE user_id=? AND status='pending'",
                (now, user_id),
            )
            cur = db.execute(
                """INSERT INTO clarification_requests(user_id,mode,task_text,questions_json,status,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (user_id, mode, task_text, json.dumps(questions, ensure_ascii=False), "pending", now),
            )
            return int(cur.lastrowid)

    def get_pending_clarification(self, user_id: int):
        with self.conn() as db:
            row = db.execute(
                """SELECT id,mode,task_text,questions_json,created_at
                   FROM clarification_requests
                   WHERE user_id=? AND status='pending'
                   ORDER BY id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "mode": row[1],
            "task_text": row[2],
            "questions": json.loads(row[3]),
            "created_at": row[4],
        }

    def resolve_clarification(self, clarification_id: int):
        with self.conn() as db:
            db.execute(
                "UPDATE clarification_requests SET status='resolved', resolved_at=? WHERE id=? AND status='pending'",
                (self._now(), clarification_id),
            )

    def cancel_pending_clarification(self, user_id: int):
        with self.conn() as db:
            db.execute(
                "UPDATE clarification_requests SET status='cancelled', resolved_at=? WHERE user_id=? AND status='pending'",
                (self._now(), user_id),
            )
