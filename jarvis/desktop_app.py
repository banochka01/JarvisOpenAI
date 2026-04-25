import json
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from jarvis.config import DB_PATH, ROOT
from jarvis.storage.db import TASK_STATUSES, JarvisDB
from jarvis.tools.file_tool import write_file
from jarvis.tools.safe_shell import run_safe
from jarvis.tools.steam_tool import install_steam_game


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
    "new": "Новая",
    "planned": "План",
    "in_progress": "В работе",
    "testing": "Тесты",
    "needs_fix": "Нужны правки",
    "done": "Готово",
    "failed": "Ошибка",
}


class EnvEditor:
    def __init__(self, path: Path):
        self.path = path
        self.example = ROOT / ".env.example"

    def ensure(self) -> None:
        if not self.path.exists() and self.example.exists():
            shutil.copyfile(self.example, self.path)

    def read(self) -> dict[str, str]:
        self.ensure()
        data: dict[str, str] = {}
        if not self.path.exists():
            return data
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
        return data

    def write(self, values: dict[str, str]) -> None:
        self.ensure()
        existing = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        seen = set()
        lines = []
        for line in existing:
            if not line or line.strip().startswith("#") or "=" not in line:
                lines.append(line)
                continue
            key, _ = line.split("=", 1)
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


class JarvisDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Jarvis Agents Control Center")
        self.geometry("1180x740")
        self.minsize(980, 620)
        self.configure(bg="#121417")

        self.db = JarvisDB(DB_PATH)
        self.env = EnvEditor(ROOT / ".env")
        self.bot_process: subprocess.Popen | None = None
        self.selected_approval_id: int | None = None

        self._build_style()
        self._build_layout()
        self.refresh_all()
        self.after(2500, self._tick)

    def _build_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background="#121417", foreground="#E8EAED", font=("Segoe UI", 10))
        style.configure("TFrame", background="#121417")
        style.configure("Surface.TFrame", background="#1B1F24")
        style.configure("TLabel", background="#121417", foreground="#E8EAED")
        style.configure("Muted.TLabel", foreground="#9AA3AF")
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18), foreground="#F4F7FB")
        style.configure("Section.TLabel", font=("Segoe UI Semibold", 11), foreground="#F4F7FB")
        style.configure("TButton", background="#2B3138", foreground="#F4F7FB", borderwidth=0, padding=(12, 7))
        style.map("TButton", background=[("active", "#3A424C")])
        style.configure("Accent.TButton", background="#2F7D68", foreground="#FFFFFF")
        style.map("Accent.TButton", background=[("active", "#3A967E")])
        style.configure("Danger.TButton", background="#8B3A3A", foreground="#FFFFFF")
        style.map("Danger.TButton", background=[("active", "#A34848")])
        style.configure("Treeview", background="#171B20", fieldbackground="#171B20", foreground="#E8EAED", rowheight=28)
        style.configure("Treeview.Heading", background="#242A31", foreground="#F4F7FB", font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#315D73")])
        style.configure("Horizontal.TProgressbar", background="#2F7D68", troughcolor="#272D34", bordercolor="#272D34")

    def _build_layout(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="Jarvis Agents", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="Local orchestrator dashboard", style="Muted.TLabel").pack(side="left", padx=(12, 0), pady=(7, 0))
        ttk.Button(header, text="Обновить", command=self.refresh_all).pack(side="right")
        ttk.Button(header, text="Запустить бота", style="Accent.TButton", command=self.start_bot).pack(side="right", padx=8)
        ttk.Button(header, text="Остановить", style="Danger.TButton", command=self.stop_bot).pack(side="right")

        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(root, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(8, 12))

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self._build_dashboard_tab()
        self._build_approvals_tab()
        self._build_settings_tab()
        self._build_logs_tab()

    def _build_dashboard_tab(self):
        tab = ttk.Frame(self.tabs, padding=14, style="Surface.TFrame")
        self.tabs.add(tab, text="Прогресс")

        columns = ("id", "status", "agent", "progress", "title", "updated")
        self.tasks_tree = ttk.Treeview(tab, columns=columns, show="headings", height=12)
        for column, title, width in (
            ("id", "#", 60),
            ("status", "Статус", 120),
            ("agent", "Агент", 110),
            ("progress", "%", 60),
            ("title", "Задача", 470),
            ("updated", "Обновлено", 160),
        ):
            self.tasks_tree.heading(column, text=title)
            self.tasks_tree.column(column, width=width, anchor="w")
        self.tasks_tree.pack(fill="both", expand=True)

        footer = ttk.Frame(tab, style="Surface.TFrame")
        footer.pack(fill="x", pady=(14, 0))
        ttk.Label(footer, text="Общий прогресс", style="Section.TLabel").pack(anchor="w")
        self.progress = ttk.Progressbar(footer, maximum=100)
        self.progress.pack(fill="x", pady=(6, 4))
        self.progress_text = tk.StringVar(value="Нет задач")
        ttk.Label(footer, textvariable=self.progress_text, style="Muted.TLabel").pack(anchor="w")

    def _build_approvals_tab(self):
        tab = ttk.Frame(self.tabs, padding=14, style="Surface.TFrame")
        self.tabs.add(tab, text="Approvals")

        paned = ttk.PanedWindow(tab, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, style="Surface.TFrame")
        right = ttk.Frame(paned, style="Surface.TFrame")
        paned.add(left, weight=2)
        paned.add(right, weight=3)

        columns = ("id", "type", "status", "created")
        self.approvals_tree = ttk.Treeview(left, columns=columns, show="headings", height=15)
        for column, title, width in (
            ("id", "#", 60),
            ("type", "Тип", 120),
            ("status", "Статус", 100),
            ("created", "Создано", 150),
        ):
            self.approvals_tree.heading(column, text=title)
            self.approvals_tree.column(column, width=width, anchor="w")
        self.approvals_tree.bind("<<TreeviewSelect>>", self.on_approval_select)
        self.approvals_tree.pack(fill="both", expand=True)

        actions = ttk.Frame(left, style="Surface.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Approve", style="Accent.TButton", command=self.approve_selected).pack(side="left")
        ttk.Button(actions, text="Cancel", style="Danger.TButton", command=self.cancel_selected).pack(side="left", padx=8)

        ttk.Label(right, text="Payload / результат", style="Section.TLabel").pack(anchor="w")
        self.approval_text = tk.Text(right, bg="#101317", fg="#E8EAED", insertbackground="#E8EAED", relief="flat", wrap="word")
        self.approval_text.pack(fill="both", expand=True, pady=(8, 0))

    def _build_settings_tab(self):
        tab = ttk.Frame(self.tabs, padding=14, style="Surface.TFrame")
        self.tabs.add(tab, text="Настройки")
        self.setting_vars: dict[str, tk.StringVar] = {}
        fields = [
            "TELEGRAM_BOT_TOKEN",
            "ALLOWED_USER_ID",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "WORKSPACE",
            "DB_PATH",
            "AUTO_APPROVE_SAFE_COMMANDS",
        ]
        for row, key in enumerate(fields):
            ttk.Label(tab, text=key, style="Section.TLabel").grid(row=row, column=0, sticky="w", pady=7, padx=(0, 12))
            var = tk.StringVar()
            self.setting_vars[key] = var
            entry = ttk.Entry(tab, textvariable=var, width=82, show="*" if key.endswith("TOKEN") or key.endswith("KEY") else "")
            entry.grid(row=row, column=1, sticky="ew", pady=7)
        tab.columnconfigure(1, weight=1)

        buttons = ttk.Frame(tab, style="Surface.TFrame")
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(buttons, text="Сохранить .env", style="Accent.TButton", command=self.save_settings).pack(side="left")
        ttk.Button(buttons, text="Перезагрузить", command=self.load_settings).pack(side="left", padx=8)

        ttk.Label(
            tab,
            text="После изменения токенов перезапусти Telegram-бота. Workspace и DB должны оставаться внутри папки проекта.",
            style="Muted.TLabel",
        ).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_logs_tab(self):
        tab = ttk.Frame(self.tabs, padding=14, style="Surface.TFrame")
        self.tabs.add(tab, text="Логи")
        self.logs_text = tk.Text(tab, bg="#101317", fg="#E8EAED", insertbackground="#E8EAED", relief="flat", wrap="word")
        self.logs_text.pack(fill="both", expand=True)

    def load_settings(self):
        values = self.env.read()
        for key, var in self.setting_vars.items():
            var.set(values.get(key, ""))
        self.status_var.set("Настройки загружены")

    def save_settings(self):
        values = {key: var.get().strip() for key, var in self.setting_vars.items()}
        self.env.write(values)
        messagebox.showinfo("Jarvis", "Настройки сохранены. Перезапусти бота, если менял токены или пути.")
        self.status_var.set("Настройки сохранены")

    def refresh_tasks(self):
        self.tasks_tree.delete(*self.tasks_tree.get_children())
        rows = self.db.list_task_rows()
        total = 0
        for row in rows:
            task_id, title, _description, status, agent, _created, updated = row
            value = STATUS_PROGRESS.get(status, 0)
            total += value
            self.tasks_tree.insert("", "end", values=(
                task_id,
                STATUS_LABELS.get(status, status),
                agent or "agent?",
                value,
                title,
                updated,
            ))
        average = int(total / len(rows)) if rows else 0
        self.progress["value"] = average
        self.progress_text.set(f"{average}% по {len(rows)} задачам" if rows else "Нет задач")

    def refresh_approvals(self):
        current = self.selected_approval_id
        self.approvals_tree.delete(*self.approvals_tree.get_children())
        for row in self.db.list_approvals(limit=100):
            approval_id, _user_id, action_type, _payload_json, status, created, _decided = row
            self.approvals_tree.insert("", "end", iid=str(approval_id), values=(approval_id, action_type, status, created))
        if current and self.approvals_tree.exists(str(current)):
            self.approvals_tree.selection_set(str(current))

    def refresh_logs(self):
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", "end")
        for log_id, kind, content, created in self.db.list_logs(limit=80):
            self.logs_text.insert("end", f"[{created}] #{log_id} {kind}\n{content}\n\n")
        self.logs_text.configure(state="disabled")

    def refresh_all(self):
        self.load_settings()
        self.refresh_tasks()
        self.refresh_approvals()
        self.refresh_logs()
        self.status_var.set("Обновлено")

    def on_approval_select(self, _event=None):
        selected = self.approvals_tree.selection()
        if not selected:
            return
        self.selected_approval_id = int(selected[0])
        approval = self.db.get_approval_any_user(self.selected_approval_id)
        self.approval_text.configure(state="normal")
        self.approval_text.delete("1.0", "end")
        self.approval_text.insert("end", json.dumps(approval, ensure_ascii=False, indent=2))
        self.approval_text.configure(state="disabled")

    def _execute_approval(self, approval_id: int) -> str:
        approval = self.db.get_approval_any_user(approval_id)
        if not approval:
            return "Approval не найден."
        if approval["status"] != "pending":
            return f"Approval уже в статусе {approval['status']}."
        payload = approval["payload"]
        self.db.decide_approval(approval_id, "approved")
        if approval["action_type"] == "shell":
            return run_safe(payload["command"])
        if approval["action_type"] == "write_file":
            return write_file(payload["path"], payload["content"])
        if approval["action_type"] == "steam":
            return install_steam_game(payload["app_id"])
        if approval["action_type"] == "mark_done":
            self.db.set_task_status(int(payload["task_id"]), "done")
            return f"Задача #{payload['task_id']} отмечена done."
        return "Неизвестный approval action."

    def approve_selected(self):
        if not self.selected_approval_id:
            messagebox.showwarning("Jarvis", "Выбери approval.")
            return
        result = self._execute_approval(self.selected_approval_id)
        self.db.log("desktop:approval", f"approved #{self.selected_approval_id}\n{result}")
        self.refresh_all()
        self.approval_text.configure(state="normal")
        self.approval_text.delete("1.0", "end")
        self.approval_text.insert("end", result)
        self.approval_text.configure(state="disabled")

    def cancel_selected(self):
        if not self.selected_approval_id:
            messagebox.showwarning("Jarvis", "Выбери approval.")
            return
        self.db.decide_approval(self.selected_approval_id, "cancelled")
        self.db.log("desktop:approval", f"cancelled #{self.selected_approval_id}")
        self.refresh_all()

    def start_bot(self):
        if self.bot_process and self.bot_process.poll() is None:
            self.status_var.set("Бот уже запущен из этого окна")
            return
        self.bot_process = subprocess.Popen([sys.executable, "-m", "jarvis.bot"], cwd=ROOT)
        self.status_var.set("Бот запущен")

    def stop_bot(self):
        if self.bot_process and self.bot_process.poll() is None:
            self.bot_process.terminate()
            self.status_var.set("Бот остановлен")
            return
        self.status_var.set("Нет процесса бота, запущенного из этого окна")

    def _tick(self):
        try:
            self.refresh_tasks()
            self.refresh_approvals()
        finally:
            self.after(2500, self._tick)

    def destroy(self):
        self.stop_bot()
        super().destroy()


def main():
    app = JarvisDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
