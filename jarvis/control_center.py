from __future__ import annotations

import argparse
import html
import json
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jarvis.config import DB_PATH, ROOT
from jarvis.storage.db import JarvisDB
from jarvis.tools.file_tool import write_file
from jarvis.tools.safe_shell import run_safe
from jarvis.tools.steam_tool import install_steam_game


MASKED_SECRET_VALUE = "********"

STATUS_PROGRESS = {
    "new": 8,
    "planned": 22,
    "in_progress": 48,
    "testing": 72,
    "needs_fix": 58,
    "done": 100,
    "failed": 100,
}

STATUS_LABELS = {
    "new": "Pending",
    "planned": "Planned",
    "in_progress": "In progress",
    "testing": "Testing",
    "needs_fix": "Needs fix",
    "done": "Done",
    "failed": "Failed",
}

SETTINGS_SECTIONS = [
    {
        "title": "Telegram",
        "items": [
            {"key": "TELEGRAM_BOT_TOKEN", "hint": "Токен Telegram-бота из @BotFather.", "secret": True},
            {"key": "ALLOWED_USER_ID", "hint": "Твой Telegram user id, которому разрешено управлять ботом.", "secret": False},
        ],
    },
    {
        "title": "OpenAI",
        "items": [
            {"key": "OPENAI_API_KEY", "hint": "API key для текстовых агентов и fallback STT.", "secret": True},
            {"key": "OPENAI_MODEL", "hint": "Модель для планирования, build и команд.", "secret": False},
        ],
    },
    {
        "title": "Storage",
        "items": [
            {"key": "WORKSPACE", "hint": "Рабочая папка внутри проекта.", "secret": False},
            {"key": "DB_PATH", "hint": "Путь к SQLite базе внутри проекта.", "secret": False},
        ],
    },
    {
        "title": "Safety",
        "items": [
            {
                "key": "AUTO_APPROVE_SAFE_COMMANDS",
                "hint": "1 разрешает read-only shell-команды без approval, 0 требует подтверждение.",
                "secret": False,
            },
        ],
    },
    {
        "title": "Speech To Text",
        "items": [
            {"key": "STT_PROVIDER", "hint": "openai или server.", "secret": False},
            {"key": "STT_SERVER_URL", "hint": "URL VPS STT endpoint, например https://host/transcribe.", "secret": False},
            {"key": "STT_SERVER_TOKEN", "hint": "Bearer token для VPS STT сервера.", "secret": True},
            {"key": "STT_SERVER_TIMEOUT", "hint": "Таймаут распознавания в секундах.", "secret": False},
        ],
    },
    {
        "title": "Proxy",
        "items": [
            {"key": "HTTP_PROXY", "hint": "HTTP proxy, если нужен.", "secret": True},
            {"key": "HTTPS_PROXY", "hint": "HTTPS proxy, если нужен.", "secret": True},
            {"key": "NO_PROXY", "hint": "Хосты без proxy, например 127.0.0.1,localhost.", "secret": False},
        ],
    },
    {
        "title": "PC App Shortcuts",
        "items": [
            {"key": "PC_APP_YANDEX_MUSIC_PATH", "hint": "Путь к .lnk или .exe Яндекс Музыки.", "secret": False},
            {"key": "PC_APP_SPOTIFY_PATH", "hint": "Путь к .lnk или .exe Spotify.", "secret": False},
            {"key": "PC_APP_VALORANT_PATH", "hint": "Путь к .lnk или .exe VALORANT/Riot.", "secret": False},
            {"key": "PC_APP_AYUGRAM_PATH", "hint": "Путь к .lnk или .exe AyuGram.", "secret": False},
        ],
    },
]


def secret_setting_keys() -> set[str]:
    return {item["key"] for section in SETTINGS_SECTIONS for item in section["items"] if item.get("secret")}


class EnvEditor:
    def __init__(self, path: Path):
        self.path = path
        self.example = ROOT / ".env.example"

    def ensure(self) -> None:
        if not self.path.exists() and self.example.exists():
            shutil.copyfile(self.example, self.path)

    def read(self) -> dict[str, str]:
        self.ensure()
        if not self.path.exists():
            return {}
        data: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        return data

    def public_read(self) -> dict[str, str]:
        data = self.read()
        for key in secret_setting_keys():
            if data.get(key):
                data[key] = MASKED_SECRET_VALUE
        return data

    def write(self, values: dict[str, str]) -> None:
        self.ensure()
        existing = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        lines: list[str] = []
        seen: set[str] = set()
        for line in existing:
            if not line or line.strip().startswith("#") or "=" not in line:
                lines.append(line)
                continue
            key, _value = line.split("=", 1)
            key = key.strip()
            if key in values:
                lines.append(f"{key}={values[key]}")
                seen.add(key)
            else:
                lines.append(line)
        for key, value in values.items():
            if key not in seen:
                lines.append(f"{key}={value}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ControlCenter:
    def __init__(self) -> None:
        self.db = JarvisDB(DB_PATH)
        self.env = EnvEditor(ROOT / ".env")
        self.bot_process: subprocess.Popen | None = None
        self.session_token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        tasks = [self._task(row) for row in self.db.list_task_rows(limit=100)]
        logs = [self._log(row) for row in self.db.list_logs(limit=100)]
        approvals = [self._approval(row) for row in self.db.list_approvals(limit=100)]
        shortcuts = self.db.list_pc_shortcuts()
        pending = [item for item in approvals if item["status"] == "pending"]
        done = [item for item in tasks if item["status"] == "done"]
        active = [item for item in tasks if item["status"] not in {"done", "failed"}]
        progress = round(sum(item["progress"] for item in active) / len(active)) if active else 0
        return {
            "bot": self.bot_status(),
            "tasks": tasks,
            "approvals": approvals,
            "logs": logs,
            "shortcuts": shortcuts,
            "settings": self.env.public_read(),
            "settings_sections": SETTINGS_SECTIONS,
            "metrics": {
                "active_modules": 12,
                "total_tasks": len(tasks),
                "active_tasks": len(active),
                "done_tasks": len(done),
                "pending_approvals": len(pending),
                "automation": progress,
                "security": "HIGH",
            },
        }

    def authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        return handler.headers.get("X-Jarvis-Token") == self.session_token

    def bot_status(self) -> dict[str, Any]:
        running = bool(self.bot_process and self.bot_process.poll() is None)
        return {"running": running, "pid": self.bot_process.pid if running and self.bot_process else None}

    def start_bot(self) -> dict[str, Any]:
        with self.lock:
            if self.bot_process and self.bot_process.poll() is None:
                return {"ok": True, "message": "Bot already running.", "bot": self.bot_status()}
            self.bot_process = subprocess.Popen(
                [sys.executable, "-m", "jarvis.bot"],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.db.log("control:center", "bot started from Control Hub")
            return {"ok": True, "message": "Bot started.", "bot": self.bot_status()}

    def stop_bot(self) -> dict[str, Any]:
        with self.lock:
            if not self.bot_process or self.bot_process.poll() is not None:
                return {"ok": True, "message": "Bot is not running.", "bot": self.bot_status()}
            self.bot_process.terminate()
            try:
                self.bot_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.bot_process.kill()
                self.bot_process.wait(timeout=3)
            self.db.log("control:center", "bot stopped from Control Hub")
            return {"ok": True, "message": "Bot stopped.", "bot": self.bot_status()}

    def restart_bot(self) -> dict[str, Any]:
        self.stop_bot()
        return self.start_bot()

    def save_settings(self, values: dict[str, str]) -> dict[str, Any]:
        allowed = {item["key"] for section in SETTINGS_SECTIONS for item in section["items"]}
        existing = self.env.read()
        secrets_to_preserve = secret_setting_keys()
        clean: dict[str, str] = {}
        for key, value in values.items():
            key = str(key)
            if key not in allowed:
                continue
            value = str(value)
            if key in secrets_to_preserve and value == MASKED_SECRET_VALUE:
                clean[key] = existing.get(key, "")
            else:
                clean[key] = value
        self.env.write(clean)
        self.db.log("control:center", "settings saved from Control Hub")
        return {"ok": True, "message": "Settings saved.", "settings": self.env.public_read()}

    def execute_approval(self, approval_id: int) -> dict[str, Any]:
        approval = self.db.claim_pending_approval(approval_id)
        if not approval:
            stale = self.db.get_approval_any_user(approval_id)
            if stale:
                return {"ok": False, "message": f"Approval already {stale['status']}."}
            return {"ok": False, "message": "Approval not found."}
        payload = approval["payload"]
        try:
            if approval["action_type"] == "shell":
                output = run_safe(payload["command"])
            elif approval["action_type"] == "write_file":
                output = write_file(payload["path"], payload["content"])
            elif approval["action_type"] == "steam":
                output = install_steam_game(payload["app_id"])
            elif approval["action_type"] == "mark_done":
                self.db.set_task_status(int(payload["task_id"]), "done")
                output = f"Task #{payload['task_id']} marked as done."
            else:
                output = "Unknown approval action."
        except Exception as exc:
            self.db.finish_approval(approval_id, "failed")
            self.db.log("control:approval", f"failed #{approval_id} type={approval['action_type']}\n{exc!r}")
            return {"ok": False, "message": f"Approval failed: {exc}"}
        self.db.finish_approval(approval_id, "approved")
        self.db.log("control:approval", f"approved #{approval_id} type={approval['action_type']}\n{output}")
        return {"ok": True, "message": output}

    def cancel_approval(self, approval_id: int) -> dict[str, Any]:
        approval = self.db.get_approval_any_user(approval_id)
        if not approval:
            return {"ok": False, "message": "Approval not found."}
        if approval["status"] != "pending":
            return {"ok": False, "message": f"Approval already {approval['status']}."}
        self.db.decide_approval(approval_id, "cancelled")
        self.db.log("control:approval", f"cancelled #{approval_id} type={approval['action_type']}")
        return {"ok": True, "message": f"Approval #{approval_id} cancelled."}

    def _task(self, row: tuple[Any, ...]) -> dict[str, Any]:
        task_id, title, description, status, agent, created_at, updated_at = row
        return {
            "id": task_id,
            "title": title,
            "description": description or "",
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "agent": agent or "core",
            "created_at": created_at,
            "updated_at": updated_at,
            "progress": STATUS_PROGRESS.get(status, 0),
        }

    def _log(self, row: tuple[Any, ...]) -> dict[str, Any]:
        log_id, kind, content, created_at = row
        return {"id": log_id, "kind": kind, "content": content, "created_at": created_at}

    def _approval(self, row: tuple[Any, ...]) -> dict[str, Any]:
        approval_id, user_id, action_type, payload_json, status, created_at, decided_at = row
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {"raw": payload_json}
        return {
            "id": approval_id,
            "user_id": user_id,
            "action_type": action_type,
            "payload": payload,
            "summary": approval_summary(action_type, payload),
            "status": status,
            "created_at": created_at,
            "decided_at": decided_at,
        }


def approval_summary(action_type: str, payload: dict[str, Any]) -> str:
    if action_type == "shell":
        return f"Shell: {payload.get('command', '')}"
    if action_type == "write_file":
        return f"Write file: {payload.get('path', '')}"
    if action_type == "steam":
        return f"Steam app: {payload.get('app_id', '')}"
    if action_type == "mark_done":
        return f"Mark task #{payload.get('task_id', '')} done"
    return json.dumps(payload, ensure_ascii=False)


INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Jarvis OpenAI Control Hub</title>
  <style>
    :root {
      --bg: #06000b;
      --surface: #100519;
      --surface-2: #150b20;
      --card: #13091f;
      --card-2: #1b1127;
      --line: #51207a;
      --line-soft: #2a143c;
      --purple: #a23dff;
      --purple-2: #cc70ff;
      --purple-3: #7928ff;
      --green: #23ff91;
      --yellow: #d6b149;
      --red: #ff5e8b;
      --text: #fbf7ff;
      --muted: #b6a6c9;
      --dim: #756785;
      --body-glow: rgba(119, 41, 255, .16);
      --body-start: #08010e;
      --body-end: #050009;
      --panel-bg: rgba(15, 6, 25, .94);
      --card-bg: rgba(27, 17, 39, .8);
      --box-bg: rgba(22, 10, 34, .76);
      --nav-bg: rgba(24, 14, 36, .76);
      --nav-active: rgba(91, 32, 132, .92);
      --input-bg: rgba(7, 2, 12, .88);
      --ghost-bg: rgba(18, 8, 30, .82);
      --danger-bg: rgba(130, 20, 58, .8);
      --bar-start: #d47aff;
      --bar-mid: #9f45ff;
      --bar-end: #7628ff;
      --shadow: rgba(0, 0, 0, .18);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at 42% -12%, var(--body-glow), transparent 34rem),
        linear-gradient(180deg, var(--body-start) 0%, var(--body-end) 100%);
      letter-spacing: 0;
    }
    button, input, textarea { font: inherit; }
    button {
      border: 1px solid var(--line);
      color: var(--text);
      background: var(--purple);
      cursor: pointer;
      transition: transform .16s ease, border-color .16s ease, background .16s ease;
    }
    button:hover { transform: translateY(-1px); border-color: var(--purple-2); }
    .desktop {
      min-height: 100vh;
      padding: 10px 24px;
      display: flex;
      justify-content: center;
    }
    .shell {
      width: min(100%, 1050px);
      display: grid;
      grid-template-columns: 176px minmax(0, 1fr);
      gap: 14px;
      align-items: start;
    }
    .sidebar, .panel, .hero, .metric, .task-card, .module-pill, .setting-group, .log-row, .approval-card {
      background: var(--panel-bg);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: 0 0 0 1px rgba(117, 43, 178, .05), 0 20px 80px var(--shadow);
    }
    .sidebar {
      min-height: calc(100vh - 20px);
      position: sticky;
      top: 10px;
      padding: 18px 12px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .brand {
      display: grid;
      grid-template-columns: 32px 1fr;
      gap: 10px;
      align-items: center;
      padding: 0 6px 10px;
    }
    .logo {
      width: 28px;
      height: 28px;
      border-radius: 9px;
      display: grid;
      place-items: center;
      background: linear-gradient(180deg, var(--purple-2), var(--purple));
      font-weight: 900;
    }
    .brand strong { display: block; font-size: 14px; line-height: 1; }
    .brand span { display: block; color: var(--muted); font-size: 9px; margin-top: 4px; }
    .nav { display: flex; flex-direction: column; gap: 8px; }
    .nav button {
      width: 100%;
      height: 32px;
      border-radius: 7px;
      padding: 0 12px;
      text-align: left;
      background: var(--nav-bg);
      border-color: transparent;
      font-size: 11px;
      font-weight: 700;
    }
    .nav button.active {
      background: var(--nav-active);
      border-color: var(--purple-2);
    }
    .status-box, .notes-box {
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--box-bg);
    }
    .status-box small, .notes-box small { display: block; color: var(--muted); font-size: 9px; margin-bottom: 7px; }
    .status-box .online { color: var(--green); font-weight: 900; font-size: 19px; line-height: 1; }
    .status-box p, .notes-box p { color: var(--muted); font-size: 10px; line-height: 1.25; margin: 6px 0 0; }
    .sidebar-footer { margin-top: auto; color: var(--muted); font-size: 9px; text-align: center; padding-top: 24px; }
    main { padding-bottom: 18px; }
    .hero {
      min-height: 182px;
      padding: 30px 28px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: center;
    }
    .eyebrow {
      color: var(--purple-2);
      text-transform: uppercase;
      font-size: 10px;
      letter-spacing: .08em;
      font-weight: 800;
      margin-bottom: 12px;
    }
    h1 {
      margin: 0;
      max-width: 520px;
      font-size: clamp(34px, 4.7vw, 52px);
      line-height: .96;
      letter-spacing: 0;
      text-shadow: 3px 2px 0 rgba(124, 39, 255, .28);
    }
    .hero p { max-width: 520px; color: var(--muted); font-size: 13px; line-height: 1.32; margin: 16px 0 0; }
    .hero-actions { display: flex; flex-direction: column; gap: 10px; min-width: 96px; }
    .primary, .ghost, .mini-btn {
      min-height: 32px;
      border-radius: 7px;
      padding: 0 15px;
      font-size: 10px;
      font-weight: 900;
    }
    .ghost { background: var(--ghost-bg); }
    .mini-btn { min-height: 26px; padding: 0 10px; }
    .mini-btn.danger { background: var(--danger-bg); border-color: rgba(255, 94, 139, .65); }
    .grid { display: grid; gap: 14px; margin-top: 14px; }
    .metrics { grid-template-columns: repeat(4, 1fr); }
    .metric { min-height: 126px; padding: 22px 16px; }
    .metric span { color: var(--muted); font-size: 10px; display: block; }
    .metric strong { display: block; font-size: 29px; line-height: 1; margin: 32px 0 22px; }
    .metric p { color: var(--muted); font-size: 11px; margin: 0; }
    .two-col { grid-template-columns: 1.24fr 1fr; }
    .panel { padding: 18px; min-height: 220px; }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 14px;
    }
    .panel h2 { margin: 0; font-size: 16px; line-height: 1; }
    .badge {
      color: var(--text);
      background: rgba(104, 38, 151, .82);
      border: 1px solid rgba(196, 105, 255, .45);
      border-radius: 999px;
      font-size: 9px;
      font-weight: 900;
      padding: 6px 10px;
    }
    .task-list, .approval-list, .log-list { display: flex; flex-direction: column; gap: 10px; }
    .task-card, .approval-card, .log-row {
      border-color: var(--line-soft);
      background: var(--card-bg);
      padding: 13px 13px;
    }
    .task-card { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
    .task-card h3, .approval-card h3 { margin: 0 0 7px; font-size: 13px; line-height: 1.2; }
    .task-card p, .approval-card p, .log-row p { color: var(--muted); margin: 0; font-size: 10px; line-height: 1.35; }
    .status {
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 9px;
      font-weight: 900;
      background: rgba(83, 35, 121, .88);
      color: var(--text);
    }
    .status.done { background: rgba(20, 111, 75, .88); }
    .status.in_progress, .status.testing { background: rgba(127, 86, 15, .88); }
    .status.failed, .status.needs_fix { background: rgba(132, 28, 62, .88); }
    .modules { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .module-pill {
      min-height: 35px;
      border-color: var(--line-soft);
      background: var(--card-bg);
      padding: 10px 12px;
      font-size: 11px;
      font-weight: 850;
    }
    .chart {
      height: 122px;
      border: 1px solid var(--line-soft);
      border-radius: 10px;
      background: var(--card-bg);
      padding: 30px 10px 10px;
      display: grid;
      grid-template-columns: repeat(8, 1fr);
      align-items: end;
      gap: 6px;
      overflow: hidden;
    }
    .bar {
      min-height: 16px;
      border-radius: 8px 8px 3px 3px;
      background: linear-gradient(180deg, var(--bar-start) 0%, var(--bar-mid) 48%, var(--bar-end) 100%);
      box-shadow: 0 0 28px rgba(159, 69, 255, .42);
    }
    .caption { margin-top: 10px; color: var(--muted); font-size: 10px; }
    .quick-actions { max-width: 390px; }
    .quick-actions .action-stack { display: flex; flex-direction: column; gap: 8px; }
    .quick-actions button { width: 100%; height: 30px; border-radius: 8px; font-size: 10px; font-weight: 900; }
    .page { display: none; }
    .page.active { display: block; }
    .settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .setting-group { padding: 16px; border-color: var(--line-soft); }
    .setting-group h3 { margin: 0 0 12px; font-size: 13px; }
    label { display: block; margin-top: 10px; color: var(--muted); font-size: 10px; }
    input {
      width: 100%;
      min-height: 34px;
      margin-top: 5px;
      padding: 8px 10px;
      border: 1px solid var(--line-soft);
      border-radius: 7px;
      background: var(--input-bg);
      color: var(--text);
      outline: none;
    }
    input:focus { border-color: var(--purple-2); box-shadow: 0 0 0 2px rgba(162, 61, 255, .16); }
    .hint { margin-top: 4px; color: var(--dim); font-size: 9px; line-height: 1.25; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.35;
    }
    .empty {
      color: var(--muted);
      border: 1px dashed var(--line-soft);
      border-radius: 10px;
      padding: 18px;
      font-size: 12px;
    }
    .theme-grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 14px; align-items: start; }
    .preset-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .preset-card {
      min-height: 74px;
      border: 1px solid var(--line-soft);
      border-radius: 10px;
      background: var(--card-bg);
      padding: 12px;
      display: grid;
      gap: 9px;
      text-align: left;
      color: var(--text);
    }
    .preset-card strong { font-size: 12px; }
    .preset-card span { color: var(--muted); font-size: 10px; }
    .swatches { display: flex; gap: 6px; }
    .swatch { width: 22px; height: 14px; border-radius: 999px; border: 1px solid rgba(255,255,255,.18); }
    .color-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .color-control {
      border: 1px solid var(--line-soft);
      border-radius: 10px;
      background: var(--card-bg);
      padding: 10px;
    }
    .color-control label { margin-top: 0; }
    input[type="color"] {
      height: 38px;
      padding: 4px;
      cursor: pointer;
    }
    .theme-preview {
      min-height: 174px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background:
        radial-gradient(circle at 28% 0%, var(--body-glow), transparent 14rem),
        linear-gradient(180deg, var(--body-start), var(--body-end));
      padding: 14px;
      display: grid;
      align-content: end;
      gap: 10px;
    }
    .theme-preview-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-bg);
      padding: 12px;
    }
    .theme-preview-card strong { display: block; font-size: 16px; margin-bottom: 8px; }
    .theme-preview-card p { margin: 0; color: var(--muted); font-size: 10px; }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: 360px;
      padding: 12px 14px;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: rgba(20, 8, 31, .96);
      color: var(--text);
      font-size: 12px;
      opacity: 0;
      transform: translateY(10px);
      pointer-events: none;
      transition: opacity .18s ease, transform .18s ease;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    @media (max-width: 820px) {
      .desktop { padding: 8px; }
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; min-height: auto; }
      .nav { display: grid; grid-template-columns: repeat(3, 1fr); }
      .hero, .two-col, .metrics, .settings-grid, .theme-grid, .preset-grid, .color-grid { grid-template-columns: 1fr; }
      .hero { padding: 24px 18px; }
      .hero-actions { flex-direction: row; }
    }
  </style>
</head>
<body>
  <div class="desktop">
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="logo">J</div>
          <div><strong>JARVIS</strong><span>OpenAI Control Hub</span></div>
        </div>
        <nav class="nav" id="nav"></nav>
        <div class="status-box">
          <small>System Status</small>
          <div class="online">ONLINE</div>
          <p id="statusText">Tasks: 0 · Active: 0 · Done: 0</p>
        </div>
        <div class="notes-box">
          <small>Quick Notes</small>
          <p>Черно-фиолетовый интерфейс, голосовые команды и локальные app shortcuts.</p>
        </div>
        <div class="sidebar-footer">Settings loaded</div>
      </aside>

      <main>
        <section class="hero">
          <div>
            <div class="eyebrow">Dashboard / Jarvis OpenAI</div>
            <h1>Панель управления Джарвисом</h1>
            <p>Футуристичный интерфейс для Telegram-бота, голосового STT, approvals и локальной автоматизации.</p>
          </div>
          <div class="hero-actions">
            <button class="primary" id="botToggle">Launch Core</button>
            <button class="ghost" data-page="logs">View Logs</button>
          </div>
        </section>

        <section class="page active" id="page-overview">
          <div class="grid metrics" id="metrics"></div>
          <div class="grid two-col">
            <div class="panel">
              <div class="panel-head"><h2>Task Queue</h2><span class="badge">Live</span></div>
              <div class="task-list" id="overviewTasks"></div>
            </div>
            <div class="panel">
              <div class="panel-head"><h2>System Modules</h2><span class="badge">Core</span></div>
              <div class="modules" id="modules"></div>
            </div>
          </div>
          <div class="grid">
            <div class="panel">
              <div class="panel-head"><h2>Activity Monitor</h2><span class="badge">Realtime</span></div>
              <div class="chart" id="chart"></div>
              <div class="caption" id="activityCaption">CPU: visual · Memory: synced · Pending approvals: 0</div>
            </div>
            <div class="panel quick-actions">
              <div class="panel-head"><h2>Quick Actions</h2><span class="badge">Safe</span></div>
              <div class="action-stack">
                <button data-action="restart">Restart bot core</button>
                <button data-page="settings">Configure variables</button>
                <button data-page="theme">Tune theme</button>
                <button data-page="approvals">Review approvals</button>
              </div>
            </div>
          </div>
        </section>

        <section class="page" id="page-tasks">
          <div class="panel"><div class="panel-head"><h2>Tasks</h2><span class="badge">Queue</span></div><div class="task-list" id="allTasks"></div></div>
        </section>
        <section class="page" id="page-systems">
          <div class="grid two-col">
            <div class="panel"><div class="panel-head"><h2>PC Shortcuts</h2><span class="badge">Voice</span></div><div class="modules" id="shortcuts"></div></div>
            <div class="panel"><div class="panel-head"><h2>Core Modules</h2><span class="badge">Ready</span></div><div class="modules" id="modulesFull"></div></div>
          </div>
        </section>
        <section class="page" id="page-approvals">
          <div class="panel"><div class="panel-head"><h2>Approvals</h2><span class="badge">Restricted</span></div><div class="approval-list" id="approvals"></div></div>
        </section>
        <section class="page" id="page-activity">
          <div class="panel"><div class="panel-head"><h2>Activity Monitor</h2><span class="badge">Realtime</span></div><div class="chart" id="chartBig"></div><div class="caption" id="activityCaptionBig"></div></div>
        </section>
        <section class="page" id="page-settings">
          <form class="panel" id="settingsForm">
            <div class="panel-head"><h2>Settings</h2><button class="mini-btn" type="submit">Save Env</button></div>
            <div class="settings-grid" id="settings"></div>
          </form>
        </section>
        <section class="page" id="page-theme">
          <div class="panel">
            <div class="panel-head">
              <h2>Theme</h2>
              <button class="mini-btn" id="themeReset" type="button">Reset</button>
            </div>
            <div class="theme-grid">
              <div>
                <div class="panel-head"><h2>Presets</h2><span class="badge">Local</span></div>
                <div class="preset-grid" id="themePresets"></div>
              </div>
              <div>
                <div class="panel-head"><h2>Preview</h2><span class="badge" id="themeModeBadge">Dark</span></div>
                <div class="theme-preview">
                  <div class="theme-preview-card">
                    <strong>Jarvis Control Hub</strong>
                    <p>Theme changes apply instantly and stay in this browser.</p>
                  </div>
                </div>
              </div>
            </div>
            <div class="grid">
              <div>
                <div class="panel-head"><h2>Custom Colors</h2><span class="badge">Manual</span></div>
                <div class="color-grid" id="themeColors"></div>
              </div>
            </div>
          </div>
        </section>
        <section class="page" id="page-logs">
          <div class="panel"><div class="panel-head"><h2>Logs</h2><span class="badge">Stream</span></div><div class="log-list" id="logs"></div></div>
        </section>
      </main>
    </div>
  </div>
  <div class="toast" id="toast"></div>
  <script>
    const pages = [
      ["overview", "Overview"],
      ["tasks", "Tasks"],
      ["systems", "Systems"],
      ["approvals", "Approvals"],
      ["activity", "Activity"],
      ["settings", "Settings"],
      ["theme", "Theme"],
      ["logs", "Logs"]
    ];
    const modules = ["Voice Engine Ready", "Vision Idle", "Memory Synced", "Automation Active", "VPS STT Linked", "PC Shortcuts"];
    const chartValues = [34, 50, 66, 42, 74, 56, 82, 61];
    const sessionToken = "__JARVIS_SESSION_TOKEN__";
    const themeStorageKey = "jarvis-control-theme";
    const themeFields = [
      ["bg", "Background"],
      ["body-start", "Body start"],
      ["body-end", "Body end"],
      ["surface", "Surface"],
      ["card", "Card"],
      ["line", "Border"],
      ["purple", "Primary"],
      ["purple-2", "Accent"],
      ["text", "Text"],
      ["muted", "Muted"]
    ];
    const themePresets = {
      dark: {
        label: "Dark Violet",
        note: "Original Jarvis look",
        mode: "Dark",
        colors: {
          "bg": "#06000b", "body-start": "#08010e", "body-end": "#050009", "surface": "#100519",
          "card": "#13091f", "line": "#51207a", "line-soft": "#2a143c", "purple": "#a23dff",
          "purple-2": "#cc70ff", "purple-3": "#7928ff", "text": "#fbf7ff", "muted": "#b6a6c9",
          "dim": "#756785", "green": "#23ff91", "yellow": "#d6b149", "red": "#ff5e8b",
          "bar-start": "#d47aff", "bar-mid": "#9f45ff", "bar-end": "#7628ff"
        },
        extras: {
          "body-glow": "rgba(119, 41, 255, .16)", "panel-bg": "rgba(15, 6, 25, .94)",
          "card-bg": "rgba(27, 17, 39, .8)", "box-bg": "rgba(22, 10, 34, .76)",
          "nav-bg": "rgba(24, 14, 36, .76)", "nav-active": "rgba(91, 32, 132, .92)",
          "input-bg": "rgba(7, 2, 12, .88)", "ghost-bg": "rgba(18, 8, 30, .82)",
          "danger-bg": "rgba(130, 20, 58, .8)", "shadow": "rgba(0, 0, 0, .18)"
        }
      },
      light: {
        label: "Light Core",
        note: "Clean bright mode",
        mode: "Light",
        colors: {
          "bg": "#f7f3ff", "body-start": "#fbf8ff", "body-end": "#ebe2ff", "surface": "#ffffff",
          "card": "#f8f3ff", "line": "#b98fff", "line-soft": "#dac6ff", "purple": "#8d31f6",
          "purple-2": "#6f23d8", "purple-3": "#b76cff", "text": "#1e1230", "muted": "#6e5d82",
          "dim": "#9384a4", "green": "#008b58", "yellow": "#8b6c00", "red": "#c03161",
          "bar-start": "#b96cff", "bar-mid": "#8b31f3", "bar-end": "#6720d1"
        },
        extras: {
          "body-glow": "rgba(141, 49, 246, .14)", "panel-bg": "rgba(255, 255, 255, .94)",
          "card-bg": "rgba(248, 243, 255, .92)", "box-bg": "rgba(247, 239, 255, .9)",
          "nav-bg": "rgba(248, 243, 255, .78)", "nav-active": "rgba(220, 198, 255, .92)",
          "input-bg": "rgba(255, 255, 255, .94)", "ghost-bg": "rgba(255, 255, 255, .72)",
          "danger-bg": "rgba(255, 229, 238, .95)", "shadow": "rgba(68, 28, 112, .12)"
        }
      },
      neon: {
        label: "Neon Console",
        note: "Purple and electric green",
        mode: "Dark",
        colors: {
          "bg": "#03070b", "body-start": "#051018", "body-end": "#020308", "surface": "#07111a",
          "card": "#0d1823", "line": "#1ee6a2", "line-soft": "#173a38", "purple": "#26f6a8",
          "purple-2": "#9a7cff", "purple-3": "#00b876", "text": "#f3fff9", "muted": "#9bbdaf",
          "dim": "#6e8d82", "green": "#32ff9f", "yellow": "#d8ff6a", "red": "#ff5c8a",
          "bar-start": "#a7ffcf", "bar-mid": "#26f6a8", "bar-end": "#8d4dff"
        },
        extras: {
          "body-glow": "rgba(38, 246, 168, .14)", "panel-bg": "rgba(7, 17, 26, .94)",
          "card-bg": "rgba(13, 24, 35, .86)", "box-bg": "rgba(7, 27, 30, .78)",
          "nav-bg": "rgba(11, 22, 31, .78)", "nav-active": "rgba(20, 86, 73, .92)",
          "input-bg": "rgba(2, 8, 12, .9)", "ghost-bg": "rgba(7, 17, 26, .82)",
          "danger-bg": "rgba(94, 20, 50, .82)", "shadow": "rgba(0, 0, 0, .22)"
        }
      },
      ember: {
        label: "Ember Glass",
        note: "Warm amber contrast",
        mode: "Dark",
        colors: {
          "bg": "#100609", "body-start": "#14070b", "body-end": "#080206", "surface": "#1a0c12",
          "card": "#241019", "line": "#8c3f62", "line-soft": "#442033", "purple": "#ff6f9e",
          "purple-2": "#ffb86c", "purple-3": "#d54276", "text": "#fff8fb", "muted": "#d5aebe",
          "dim": "#9a7383", "green": "#61ffa4", "yellow": "#ffd36a", "red": "#ff4f7f",
          "bar-start": "#ffbd75", "bar-mid": "#ff6f9e", "bar-end": "#ad45ff"
        },
        extras: {
          "body-glow": "rgba(255, 111, 158, .14)", "panel-bg": "rgba(26, 12, 18, .94)",
          "card-bg": "rgba(36, 16, 25, .86)", "box-bg": "rgba(40, 16, 25, .76)",
          "nav-bg": "rgba(31, 13, 21, .78)", "nav-active": "rgba(120, 47, 78, .92)",
          "input-bg": "rgba(12, 3, 7, .9)", "ghost-bg": "rgba(26, 12, 18, .82)",
          "danger-bg": "rgba(130, 20, 58, .86)", "shadow": "rgba(0, 0, 0, .2)"
        }
      }
    };
    let appState = null;
    let activePage = "overview";
    let activeTheme = null;

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function toast(message) {
      const el = document.getElementById("toast");
      el.textContent = message;
      el.classList.add("show");
      clearTimeout(window.toastTimer);
      window.toastTimer = setTimeout(() => el.classList.remove("show"), 2800);
    }

    function applyTheme(theme, persist = true, rerender = true) {
      activeTheme = JSON.parse(JSON.stringify(theme));
      theme = activeTheme;
      const root = document.documentElement;
      Object.entries(theme.colors || {}).forEach(([key, value]) => root.style.setProperty(`--${key}`, value));
      Object.entries(theme.extras || {}).forEach(([key, value]) => root.style.setProperty(`--${key}`, value));
      if (persist) localStorage.setItem(themeStorageKey, JSON.stringify(theme));
      const badge = document.getElementById("themeModeBadge");
      if (badge) badge.textContent = theme.mode || "Custom";
      if (rerender) renderThemeControls();
    }

    function loadTheme() {
      try {
        const saved = JSON.parse(localStorage.getItem(themeStorageKey) || "null");
        applyTheme(saved || themePresets.dark, false);
      } catch (_error) {
        applyTheme(themePresets.dark, false);
      }
    }

    function renderThemeControls() {
      const presetRoot = document.getElementById("themePresets");
      const colorRoot = document.getElementById("themeColors");
      if (!presetRoot || !colorRoot || !activeTheme) return;
      presetRoot.innerHTML = Object.entries(themePresets).map(([id, preset]) => {
        const colors = preset.colors;
        return `<button class="preset-card" data-theme-preset="${id}" type="button">
          <strong>${esc(preset.label)}</strong>
          <span>${esc(preset.note)}</span>
          <div class="swatches">
            <i class="swatch" style="background:${esc(colors["body-start"])}"></i>
            <i class="swatch" style="background:${esc(colors.card)}"></i>
            <i class="swatch" style="background:${esc(colors.purple)}"></i>
            <i class="swatch" style="background:${esc(colors["purple-2"])}"></i>
          </div>
        </button>`;
      }).join("");
      colorRoot.innerHTML = themeFields.map(([key, label]) => `
        <div class="color-control">
          <label for="theme-${esc(key)}">${esc(label)}</label>
          <input id="theme-${esc(key)}" data-theme-color="${esc(key)}" type="color" value="${esc(activeTheme.colors[key] || "#000000")}">
          <div class="hint">CSS variable: --${esc(key)}</div>
        </div>
      `).join("");
    }

    function hexToRgba(hex, alpha) {
      const value = hex.replace("#", "");
      const full = value.length === 3 ? value.split("").map(ch => ch + ch).join("") : value;
      const num = Number.parseInt(full, 16);
      if (Number.isNaN(num)) return hex;
      const r = (num >> 16) & 255;
      const g = (num >> 8) & 255;
      const b = num & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function updateThemeColor(key, value) {
      const next = JSON.parse(JSON.stringify(activeTheme || themePresets.dark));
      next.label = "Custom";
      next.mode = "Custom";
      next.colors[key] = value;
      if (key === "body-start") next.extras["body-glow"] = hexToRgba(value, ".18");
      if (key === "surface") {
        next.extras["panel-bg"] = hexToRgba(value, ".94");
        next.extras["box-bg"] = hexToRgba(value, ".78");
        next.extras["nav-bg"] = hexToRgba(value, ".78");
        next.extras["ghost-bg"] = hexToRgba(value, ".82");
      }
      if (key === "card") {
        next.extras["card-bg"] = hexToRgba(value, ".86");
        next.extras["input-bg"] = hexToRgba(value, ".9");
      }
      if (key === "purple") {
        next.colors["bar-mid"] = value;
        next.extras["nav-active"] = hexToRgba(value, ".42");
      }
      if (key === "purple-2") next.colors["bar-start"] = value;
      applyTheme(next, true, false);
    }

    async function request(path, options = {}) {
      const headers = {"Content-Type": "application/json", "X-Jarvis-Token": sessionToken, ...(options.headers || {})};
      const response = await fetch(path, {
        ...options,
        headers
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.message || "Request failed");
      return data;
    }

    function switchPage(page) {
      activePage = page;
      document.querySelectorAll(".page").forEach(item => item.classList.toggle("active", item.id === `page-${page}`));
      document.querySelectorAll("[data-nav]").forEach(item => item.classList.toggle("active", item.dataset.nav === page));
    }

    function renderNav() {
      document.getElementById("nav").innerHTML = pages.map(([id, label]) => (
        `<button data-nav="${id}" class="${id === activePage ? "active" : ""}">${label}</button>`
      )).join("");
    }

    function renderMetrics(metrics) {
      const cards = [
        ["Active Modules", metrics.active_modules, "Core services tracked"],
        ["Command Queue", String(metrics.pending_approvals).padStart(2, "0"), "Pending approvals"],
        ["Automation", `${metrics.automation}%`, "Optimization enabled"],
        ["Security Level", metrics.security, "Restricted access mode"]
      ];
      document.getElementById("metrics").innerHTML = cards.map(([label, value, note]) => (
        `<div class="metric"><span>${label}</span><strong>${value}</strong><p>${note}</p></div>`
      )).join("");
    }

    function taskCard(task) {
      return `<div class="task-card">
        <div><h3>#${task.id} ${esc(task.title)}</h3><p>${esc(task.agent)} · ${task.progress}% · ${esc(task.description || task.updated_at)}</p></div>
        <span class="status ${esc(task.status)}">${esc(task.status_label)}</span>
      </div>`;
    }

    function renderTasks(tasks) {
      const html = tasks.length ? tasks.map(taskCard).join("") : `<div class="empty">Task queue is empty.</div>`;
      document.getElementById("overviewTasks").innerHTML = tasks.slice(0, 3).length ? tasks.slice(0, 3).map(taskCard).join("") : `<div class="empty">Task queue is empty.</div>`;
      document.getElementById("allTasks").innerHTML = html;
    }

    function renderModules() {
      const html = modules.map(name => `<div class="module-pill">${name}</div>`).join("");
      document.getElementById("modules").innerHTML = html;
      document.getElementById("modulesFull").innerHTML = html;
    }

    function renderShortcuts(shortcuts) {
      document.getElementById("shortcuts").innerHTML = shortcuts.length
        ? shortcuts.map(item => `<div class="module-pill">${esc(item.name)}<br><small style="color:var(--muted)">${esc(item.kind)}</small></div>`).join("")
        : `<div class="empty">No shortcuts.</div>`;
    }

    function renderChart(id) {
      document.getElementById(id).innerHTML = chartValues.map(value => `<div class="bar" style="height:${value}%"></div>`).join("");
    }

    function renderApprovals(approvals) {
      document.getElementById("approvals").innerHTML = approvals.length ? approvals.map(item => {
        const pending = item.status === "pending";
        return `<div class="approval-card">
          <h3>#${item.id} ${esc(item.action_type)} <span class="status ${esc(item.status)}">${esc(item.status)}</span></h3>
          <p>${esc(item.summary)}</p>
          <pre>${esc(JSON.stringify(item.payload, null, 2))}</pre>
          ${pending ? `<p style="margin-top:10px"><button class="mini-btn" data-approve="${item.id}">Approve</button> <button class="mini-btn danger" data-cancel="${item.id}">Cancel</button></p>` : ""}
        </div>`;
      }).join("") : `<div class="empty">No approvals yet.</div>`;
    }

    function renderSettings(sections, values) {
      document.getElementById("settings").innerHTML = sections.map(section => (
        `<div class="setting-group"><h3>${esc(section.title)}</h3>` +
        section.items.map(item => `
          <label for="env-${esc(item.key)}">${esc(item.key)}</label>
          <input id="env-${esc(item.key)}" name="${esc(item.key)}" type="${item.secret ? "password" : "text"}" value="${esc(values[item.key] || "")}" autocomplete="off">
          <div class="hint">${esc(item.hint)}</div>
        `).join("") +
        `</div>`
      )).join("");
    }

    function renderLogs(logs) {
      document.getElementById("logs").innerHTML = logs.length ? logs.map(log => (
        `<div class="log-row"><p><strong>${esc(log.kind)}</strong> · ${esc(log.created_at)}</p><pre>${esc(log.content)}</pre></div>`
      )).join("") : `<div class="empty">Logs are empty.</div>`;
    }

    function render(state) {
      appState = state;
      renderMetrics(state.metrics);
      renderTasks(state.tasks);
      renderModules();
      renderShortcuts(state.shortcuts);
      renderApprovals(state.approvals);
      const editingSettings = document.activeElement && document.activeElement.closest("#settingsForm");
      if (!editingSettings) renderSettings(state.settings_sections, state.settings);
      renderLogs(state.logs);
      renderChart("chart");
      renderChart("chartBig");
      const caption = `CPU: visual · Memory: synced · Pending approvals: ${state.metrics.pending_approvals}`;
      document.getElementById("activityCaption").textContent = caption;
      document.getElementById("activityCaptionBig").textContent = caption;
      document.getElementById("statusText").textContent = `Tasks: ${state.metrics.total_tasks} · Active: ${state.metrics.active_tasks} · Done: ${state.metrics.done_tasks}`;
      document.getElementById("botToggle").textContent = state.bot.running ? "Stop Core" : "Launch Core";
    }

    async function refresh() {
      try {
        render(await request("/api/state"));
      } catch (error) {
        toast(error.message);
      }
    }

    loadTheme();
    renderNav();
    renderThemeControls();
    renderModules();
    renderChart("chart");
    renderChart("chartBig");
    switchPage(activePage);
    refresh();
    setInterval(refresh, 2500);

    document.addEventListener("click", async event => {
      const target = event.target.closest("button");
      if (!target) return;
      if (target.dataset.nav || target.dataset.page) {
        switchPage(target.dataset.nav || target.dataset.page);
        return;
      }
      if (target.dataset.themePreset) {
        applyTheme(themePresets[target.dataset.themePreset]);
        toast(`${themePresets[target.dataset.themePreset].label} applied`);
        return;
      }
      if (target.id === "themeReset") {
        localStorage.removeItem(themeStorageKey);
        applyTheme(themePresets.dark);
        toast("Theme reset");
        return;
      }
      if (target.id === "botToggle") {
        const endpoint = appState?.bot?.running ? "/api/bot/stop" : "/api/bot/start";
        try { toast((await request(endpoint, {method: "POST"})).message); await refresh(); } catch (error) { toast(error.message); }
      }
      if (target.dataset.action === "restart") {
        try { toast((await request("/api/bot/restart", {method: "POST"})).message); await refresh(); } catch (error) { toast(error.message); }
      }
      if (target.dataset.approve) {
        try { toast((await request(`/api/approvals/${target.dataset.approve}/approve`, {method: "POST"})).message); await refresh(); } catch (error) { toast(error.message); }
      }
      if (target.dataset.cancel) {
        try { toast((await request(`/api/approvals/${target.dataset.cancel}/cancel`, {method: "POST"})).message); await refresh(); } catch (error) { toast(error.message); }
      }
    });

    document.addEventListener("input", event => {
      const target = event.target.closest("[data-theme-color]");
      if (!target) return;
      updateThemeColor(target.dataset.themeColor, target.value);
    });

    document.getElementById("settingsForm").addEventListener("submit", async event => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(event.currentTarget).entries());
      try {
        toast((await request("/api/settings", {method: "POST", body: JSON.stringify({values})})).message);
        await refresh();
      } catch (error) {
        toast(error.message);
      }
    });
  </script>
</body>
</html>
"""


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status.value)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, body: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(HTTPStatus.OK.value)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    return json.loads(raw) if raw else {}


def make_handler(app: ControlCenter) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "JarvisControlHub/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            try:
                path = urlparse(self.path).path
                if path == "/":
                    html_response(self, INDEX_HTML.replace("__JARVIS_SESSION_TOKEN__", app.session_token))
                elif path == "/api/state":
                    if not app.authorized(self):
                        json_response(self, {"ok": False, "message": "Forbidden."}, HTTPStatus.FORBIDDEN)
                        return
                    json_response(self, app.state())
                else:
                    json_response(self, {"ok": False, "message": "Not found."}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                json_response(self, {"ok": False, "message": html.escape(str(exc))}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            try:
                path = urlparse(self.path).path
                if not app.authorized(self):
                    json_response(self, {"ok": False, "message": "Forbidden."}, HTTPStatus.FORBIDDEN)
                    return
                if path == "/api/bot/start":
                    json_response(self, app.start_bot())
                elif path == "/api/bot/stop":
                    json_response(self, app.stop_bot())
                elif path == "/api/bot/restart":
                    json_response(self, app.restart_bot())
                elif path == "/api/settings":
                    payload = read_body(self)
                    json_response(self, app.save_settings(payload.get("values", {})))
                elif path.startswith("/api/approvals/") and path.endswith("/approve"):
                    approval_id = int(path.split("/")[3])
                    json_response(self, app.execute_approval(approval_id))
                elif path.startswith("/api/approvals/") and path.endswith("/cancel"):
                    approval_id = int(path.split("/")[3])
                    json_response(self, app.cancel_approval(approval_id))
                else:
                    json_response(self, {"ok": False, "message": "Not found."}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                json_response(self, {"ok": False, "message": html.escape(str(exc))}, HTTPStatus.INTERNAL_SERVER_ERROR)

    return Handler


def find_free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No free local port found for Control Hub.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis OpenAI Control Hub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-remote", action="store_true", help="Allow binding Control Hub to a non-local host.")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if args.host not in local_hosts and not args.allow_remote:
        print("Refusing to bind Control Hub outside localhost without --allow-remote.")
        return 2

    port = find_free_port(args.port) if args.host in local_hosts else args.port
    app = ControlCenter()
    server = ThreadingHTTPServer((args.host, port), make_handler(app))
    url = f"http://{args.host}:{port}/"
    print(f"Jarvis Control Hub: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nControl Hub stopped.")
    finally:
        app.stop_bot()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
