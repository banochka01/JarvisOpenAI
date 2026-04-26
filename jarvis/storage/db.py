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
DEFAULT_STEAM_GAMES = [
    ("Deep Rock Galactic", "548430"),
    ("FPV Kamikaze Drone", "2707940"),
    ("A Way Out", "1222700"),
    ("Geometry Dash", "322170"),
    ("Gunfire Reborn", "1217060"),
    ("Liftoff: Micro Drones", "1432320"),
    ("LOCKDOWN Protocol", "2780980"),
    ("PUBG: BATTLEGROUNDS", "578080"),
    ("R.E.P.O.", "3241660"),
]
DEFAULT_PC_SHORTCUTS = [
    (
        "paradeevich-youtube",
        "Парадеевич на YouTube",
        "https://www.youtube.com/@paradeevich",
        ["парадеевич", "парадеевича", "paradeevich", "парадеевич ютуб", "парадеевича на ютубе"],
        "creator",
    ),
    (
        "paradeevich-twitch",
        "Парадеевич на Twitch",
        "https://www.twitch.tv/paradeev1ch",
        ["парадеевич твич", "парадеевича на твиче", "paradeevich twitch", "paradeev1ch"],
        "creator",
    ),
    ("youtube", "YouTube", "https://www.youtube.com/", ["youtube", "ютуб", "ютубе", "ютубчик"], "site"),
    ("google", "Google", "https://www.google.com/", ["google", "гугл", "гугле"], "site"),
    ("yandex", "Yandex", "https://ya.ru/", ["yandex", "яндекс", "яндексе"], "site"),
    ("twitch", "Twitch", "https://www.twitch.tv/", ["twitch", "твич", "твиче"], "site"),
    ("vk", "VK", "https://vk.com/", ["vk", "вк", "вконтакте"], "site"),
    ("telegram", "Telegram Web", "https://web.telegram.org/", ["telegram", "телеграм", "телега"], "site"),
    ("chatgpt", "ChatGPT", "https://chatgpt.com/", ["chatgpt", "чатгпт", "чат gpt", "джипити"], "site"),
    ("github", "GitHub", "https://github.com/", ["github", "гитхаб"], "site"),
    ("gmail", "Gmail", "https://mail.google.com/", ["gmail", "почта gmail", "гугл почта"], "site"),
    (
        "yandex-music",
        "Yandex Music",
        "app://yandex-music",
        ["яндекс музыка", "яндекс музыку", "яндекс музыки", "yandex music", "ya music"],
        "app",
    ),
    (
        "spotify",
        "Spotify",
        "app://spotify",
        ["spotify", "спотифай", "спотифая", "дотифай", "дотифая"],
        "app",
    ),
    (
        "valorant",
        "VALORANT",
        "app://valorant",
        ["valorant", "валорант", "валоранта", "валик", "валика"],
        "app",
    ),
    (
        "ayugram",
        "AyuGram",
        "app://ayugram",
        ["ayugram", "аюграм", "аюграмм", "аю грам", "аю грамм"],
        "app",
    ),
]


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
            db.execute("""CREATE TABLE IF NOT EXISTS steam_games (
                app_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS pc_shortcuts (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'site',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            now = self._now()
            db.executemany(
                """INSERT INTO steam_games(app_id,name,created_at,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(app_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
                [(app_id, name, now, now) for name, app_id in DEFAULT_STEAM_GAMES],
            )
            db.executemany(
                """INSERT INTO pc_shortcuts(slug,name,url,aliases_json,kind,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(slug) DO UPDATE SET
                       name=excluded.name,
                       url=excluded.url,
                       aliases_json=excluded.aliases_json,
                       kind=excluded.kind,
                       updated_at=excluded.updated_at""",
                [
                    (slug, name, url, json.dumps(aliases, ensure_ascii=False), kind, now, now)
                    for slug, name, url, aliases, kind in DEFAULT_PC_SHORTCUTS
                ],
            )
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

    def add_steam_game(self, name: str, app_id: str):
        app_id = "".join(ch for ch in str(app_id) if ch.isdigit())
        name = name.strip()
        if not name:
            raise ValueError("steam game name is required")
        if not app_id:
            raise ValueError("steam app_id must be numeric")
        now = self._now()
        with self.conn() as db:
            db.execute(
                """INSERT INTO steam_games(app_id,name,created_at,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(app_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""",
                (app_id, name, now, now),
            )

    def list_steam_games(self):
        with self.conn() as db:
            return db.execute(
                "SELECT app_id,name FROM steam_games ORDER BY name COLLATE NOCASE"
            ).fetchall()

    def get_steam_game(self, app_id: str):
        app_id = "".join(ch for ch in str(app_id) if ch.isdigit())
        if not app_id:
            return None
        with self.conn() as db:
            return db.execute(
                "SELECT app_id,name FROM steam_games WHERE app_id=?",
                (app_id,),
            ).fetchone()

    def add_pc_shortcut(self, slug: str, name: str, url: str, aliases: list[str] | None = None, kind: str = "site"):
        slug = slug.strip().lower()
        name = name.strip()
        url = url.strip()
        aliases = aliases or []
        if not slug:
            raise ValueError("pc shortcut slug is required")
        if not name:
            raise ValueError("pc shortcut name is required")
        if not url:
            raise ValueError("pc shortcut url is required")
        now = self._now()
        with self.conn() as db:
            db.execute(
                """INSERT INTO pc_shortcuts(slug,name,url,aliases_json,kind,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(slug) DO UPDATE SET
                       name=excluded.name,
                       url=excluded.url,
                       aliases_json=excluded.aliases_json,
                       kind=excluded.kind,
                       updated_at=excluded.updated_at""",
                (slug, name, url, json.dumps(aliases, ensure_ascii=False), kind, now, now),
            )

    def list_pc_shortcuts(self):
        with self.conn() as db:
            rows = db.execute(
                """SELECT slug,name,url,aliases_json,kind
                   FROM pc_shortcuts ORDER BY kind, name COLLATE NOCASE"""
            ).fetchall()
        return [
            {
                "slug": row[0],
                "name": row[1],
                "url": row[2],
                "aliases": json.loads(row[3]),
                "kind": row[4],
            }
            for row in rows
        ]

    def get_pc_shortcut(self, slug: str):
        with self.conn() as db:
            row = db.execute(
                "SELECT slug,name,url,aliases_json,kind FROM pc_shortcuts WHERE slug=?",
                (slug,),
            ).fetchone()
        if not row:
            return None
        return {
            "slug": row[0],
            "name": row[1],
            "url": row[2],
            "aliases": json.loads(row[3]),
            "kind": row[4],
        }

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

    def list_pending_approvals_for_user(self, user_id: int, limit: int = 20):
        with self.conn() as db:
            rows = db.execute(
                """SELECT id,action_type,payload_json,created_at
                   FROM approval_requests
                   WHERE user_id=? AND status='pending'
                   ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [
            {
                "id": row[0],
                "action_type": row[1],
                "payload": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]

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
