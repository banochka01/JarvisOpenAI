import asyncio
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from jarvis.approval_utils import approval_title, parse_approval_id
from jarvis.agents.llm import ask_agent, build_plan
from jarvis.agents.prompts import AGENT_PROMPTS
from jarvis.config import ALLOWED_USER_ID, AUTO_APPROVE_SAFE_COMMANDS, DB_PATH, TELEGRAM_BOT_TOKEN, validate_bot_config
from jarvis.orchestrator import Orchestrator
from jarvis.storage.db import TASK_STATUSES, JarvisDB
from jarvis.text_utils import (
    assistant_needs_clarification,
    build_clarified_task,
    chunks,
    extract_clarification_questions,
    parse_task_ref,
    strip_new_task_prefix,
)
from jarvis.tools.file_tool import list_files, preview_diff, read_file, write_file
from jarvis.tools.pc_tool import open_pc_target, resolve_pc_request
from jarvis.tools.safe_shell import READ_ONLY, classify_command, run_safe
from jarvis.tools.speech_tool import SpeechQuotaError, transcribe_audio
from jarvis.tools.steam_tool import install_steam_game, launch_steam_game


MAX_TG_TEXT = 3900
MAX_VOICE_SECONDS = 120
db = JarvisDB(DB_PATH)
orchestrator = Orchestrator(db)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 План", callback_data="menu:plan"), InlineKeyboardButton("🏗 Build", callback_data="menu:build")],
        [InlineKeyboardButton("✅ Approvals", callback_data="menu:approvals"), InlineKeyboardButton("📋 Задачи", callback_data="menu:tasks")],
        [InlineKeyboardButton("🧩 Агенты", callback_data="menu:agents"), InlineKeyboardButton("🗂 Файлы", callback_data="menu:files")],
        [InlineKeyboardButton("🛡 Проверка", callback_data="menu:security"), InlineKeyboardButton("💾 Память", callback_data="menu:memory")],
        [InlineKeyboardButton("⚙️ Shell", callback_data="menu:shell"), InlineKeyboardButton("🎮 Steam", callback_data="menu:steam")],
        [InlineKeyboardButton("🖥 PC", callback_data="menu:pc")],
    ])


def agent_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Backend", callback_data="agent:backend"), InlineKeyboardButton("Frontend", callback_data="agent:frontend")],
        [InlineKeyboardButton("Tester", callback_data="agent:tester"), InlineKeyboardButton("DevOps", callback_data="agent:devops")],
        [InlineKeyboardButton("Reviewer", callback_data="agent:reviewer"), InlineKeyboardButton("Security", callback_data="agent:security")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")],
    ])


def approval_menu(approval_id: int, ok_text: str = "✅ Выполнить"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(ok_text, callback_data=f"confirm:{approval_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"cancel:{approval_id}"),
    ]])


def steam_games_menu():
    rows = [
        [InlineKeyboardButton(name, callback_data=f"steamstart:{app_id}")]
        for app_id, name in db.list_steam_games()
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def pc_shortcuts_menu():
    rows = [
        [InlineKeyboardButton(item["name"], callback_data=f"pcopen:{item['slug']}")]
        for item in db.list_pc_shortcuts()
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def guard(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ALLOWED_USER_ID)


def _is_transient_telegram_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    module = exc.__class__.__module__.lower()
    return isinstance(exc, (NetworkError, TimedOut)) or "connecterror" in name or module.startswith("httpx")


async def telegram_call(label: str, func, *args, retries: int = 3, **kwargs):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return await func(*args, **kwargs)
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return None
            raise
        except Exception as exc:
            if not _is_transient_telegram_error(exc) or attempt == retries:
                raise
            last_error = exc
            print(f"Telegram transient error during {label}, retry {attempt}/{retries}: {exc!r}")
            await asyncio.sleep(1.5 * attempt)
    if last_error:
        raise last_error
    return None


async def answer_callback(query, text: str = "") -> None:
    try:
        kwargs = {"text": text[:180]} if text else {}
        await telegram_call("answer callback", query.answer, retries=2, **kwargs)
    except Exception as exc:
        print(f"Telegram callback answer failed, continuing: {exc!r}")


async def reply_long(update: Update, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    for part in parts[:-1]:
        await telegram_call("reply text", update.effective_message.reply_text, part)
    await telegram_call("reply text", update.effective_message.reply_text, parts[-1], reply_markup=reply_markup)


async def edit_or_reply_long(message, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    try:
        await telegram_call("edit text", message.edit_text, parts[0], reply_markup=reply_markup if len(parts) == 1 else None)
    except BadRequest:
        await telegram_call("reply text", message.reply_text, parts[0], reply_markup=reply_markup if len(parts) == 1 else None)
    for part in parts[1:-1]:
        await telegram_call("reply text", message.reply_text, part)
    if len(parts) > 1:
        await telegram_call("reply text", message.reply_text, parts[-1], reply_markup=reply_markup)


async def query_result(query, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    edited = False
    try:
        edited = await telegram_call(
            "edit callback message",
            query.edit_message_text,
            parts[0],
            reply_markup=reply_markup if len(parts) == 1 else None,
        ) is not None
    except BadRequest:
        edited = False
    if not edited:
        if query.message:
            await telegram_call(
                "reply callback text",
                query.message.reply_text,
                parts[0],
                reply_markup=reply_markup if len(parts) == 1 else None,
            )
        else:
            raise RuntimeError("callback message is not available")
    for part in parts[1:-1]:
        await telegram_call("reply callback text", query.message.reply_text, part)
    if len(parts) > 1:
        await telegram_call("reply callback text", query.message.reply_text, parts[-1], reply_markup=reply_markup)


def result_status(agent: str, answer: str) -> str:
    upper = answer.strip().upper()
    if agent in {"tester", "reviewer", "security"} and upper.startswith("BLOCKERS:"):
        return "blockers"
    return "ok"


def remember_clarification(user_id: int, mode: str, task_text: str, answer: str, questions: list[str] | None = None):
    items = questions or extract_clarification_questions(answer)
    db.create_clarification_request(user_id, task_text, items, mode=mode)


def clarification_hint() -> str:
    return "\n\nОтветь обычным сообщением — я продолжу эту же задачу. Для новой задачи начни с: новая задача:"


def format_orchestration_result(result, pending_note: str = "") -> str:
    extra = []
    if pending_note:
        extra.append(pending_note)
    if result.approvals:
        extra.append("Approvals созданы: " + ", ".join(f"#{item}" for item in result.approvals))
    if result.rejected_actions:
        extra.append("Отклонённые actions: " + str(len(result.rejected_actions)))
    text_out = f"Задача #{result.task_id}\n\n{result.text}"
    if extra:
        text_out += "\n\n" + "\n".join(extra)
    return text_out


def format_pending_approvals(user_id: int) -> str:
    rows = db.list_pending_approvals_for_user(user_id)
    if not rows:
        return "Pending approvals нет."
    lines = ["Pending approvals:"]
    for item in rows:
        lines.append(f"#{item['id']} {approval_title(item['action_type'], item['payload'])}")
    lines.append("")
    lines.append("Команды:")
    lines.append("/approve id — выполнить")
    lines.append("/cancel id — отменить")
    return "\n".join(lines)


def execute_approval(approval_id: int, user_id: int) -> str:
    action = db.get_pending_approval(approval_id, user_id)
    if not action:
        return "Действие уже не найдено/устарело."

    db.decide_approval(approval_id, "approved")
    payload = action["payload"]
    if action["action_type"] == "shell":
        out = run_safe(payload["command"])
    elif action["action_type"] == "steam":
        out = install_steam_game(payload["app_id"])
    elif action["action_type"] == "mark_done":
        db.set_task_status(int(payload["task_id"]), "done")
        out = f"✅ Задача #{payload['task_id']} отмечена done вручную."
    elif action["action_type"] == "write_file":
        out = write_file(payload["path"], payload["content"])
    else:
        out = "Неизвестное действие."
    db.log("approval:approved", f"#{approval_id} user_id={user_id} type={action['action_type']}\n{out}")
    return out


def cancel_approval(approval_id: int, user_id: int) -> str:
    action = db.get_pending_approval(approval_id, user_id)
    if not action:
        return "Действие уже не найдено/устарело."
    db.decide_approval(approval_id, "cancelled")
    db.log("approval:cancelled", f"#{approval_id} user_id={user_id} type={action['action_type']}")
    return "❌ Отменено"


async def send_approval_buttons(update: Update, approval_ids: list[int], ok_text: str = "✅ Выполнить"):
    for approval_id in approval_ids:
        approval = db.get_approval_any_user(approval_id)
        if not approval:
            continue
        payload = approval["payload"]
        title = approval_title(approval["action_type"], payload)
        label = f"Approval #{approval_id}: {title}\n\nКнопки ниже или команды: /approve {approval_id} /cancel {approval_id}"
        await telegram_call(
            "approval prompt",
            update.effective_message.reply_text,
            label,
            reply_markup=approval_menu(approval_id, ok_text),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text("Джарвис MVP+ на связи. Кидай задачу или жми кнопки.", reply_markup=main_menu())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, """Команды:
/start — меню
/plan текст — сделать безопасный план
/run текст — создать задачу, получить JSON-план и approvals
/build текст — создать готовые файлы сайта/страницы через write_file approvals
/agent backend|frontend|tester|devops|reviewer|security текст — спросить агента
/agent reviewer #3 текст — reviewer/tester результат для задачи #3
/tasks — список задач
/addtask агент | название | описание — добавить задачу
/status id статус — поставить new/planned/in_progress/testing/needs_fix/done/failed
/done id — отметить задачу done после review/test или через approve
/approvals — показать ожидающие подтверждения
/approve id — выполнить approval без кнопки
/cancel id — отменить approval без кнопки
/shell команда — команда в workspace через whitelist и approve
/gitstatus — показать git status
/gitdiff — показать git diff
/commit сообщение — создать approval на git commit
/steam app_id — открыть установку Steam через approve
/steamstart — выбрать игру из списка и запустить через Steam
/pc запрос — открыть сайт/приложение в браузере, например: /pc включи парадеевича на ютубе
Голосовое сообщение — распознать речь и выполнить PC-команду, если она понятна
/memory — показать память
/remember ключ | значение — сохранить память
/files — список файлов workspace
/read путь — прочитать файл workspace
/write путь | текст — показать diff и записать файл через approve""", reply_markup=main_menu())


async def plan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = " ".join(context.args) or update.message.text.replace("/plan", "", 1).strip()
    if not text:
        await update.message.reply_text("Напиши так: /plan сделать сайт для FPV клуба")
        return
    db.cancel_pending_clarification(update.effective_user.id)
    db.add_message("user", text)
    msg = await update.message.reply_text("🧠 Думаю план...")
    ans = build_plan(text, db.memories())
    if assistant_needs_clarification(ans):
        remember_clarification(update.effective_user.id, "plan", text, ans)
        ans += clarification_hint()
    db.log("plan", ans)
    db.add_message("assistant", ans)
    await edit_or_reply_long(msg, ans, reply_markup=main_menu())


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = " ".join(context.args) or update.message.text.replace("/run", "", 1).strip()
    if not text:
        await update.message.reply_text("Формат: /run сделать безопасный план и предложить actions")
        return
    db.cancel_pending_clarification(update.effective_user.id)
    msg = await update.message.reply_text("🧭 Собираю JSON-план и approvals...")
    result = orchestrator.plan_task(text, update.effective_user.id)
    pending_note = ""
    if result.needs_clarification:
        remember_clarification(update.effective_user.id, "run", text, result.text, result.questions or None)
        pending_note = "Жду уточнение обычным сообщением. Для новой задачи начни с: новая задача:"
    text_out = format_orchestration_result(result, pending_note)
    await edit_or_reply_long(msg, text_out, reply_markup=main_menu())


async def build_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = " ".join(context.args) or update.message.text.replace("/build", "", 1).strip()
    if not text:
        await update.message.reply_text("Формат: /build сделать лендинг для ЦТТ Новация")
        return
    db.cancel_pending_clarification(update.effective_user.id)
    msg = await update.message.reply_text("🏗 Готовлю файлы и approvals...")
    result = orchestrator.build_task(text, update.effective_user.id)
    pending_note = ""
    if result.needs_clarification:
        remember_clarification(update.effective_user.id, "build", text, result.text, result.questions or None)
        pending_note = "Жду уточнение обычным сообщением. Для новой задачи начни с: новая задача:"
    elif result.approvals:
        pending_note = "После approve файлы появятся в workspace. Посмотреть их можно через /files."
    text_out = format_orchestration_result(result, pending_note)
    await edit_or_reply_long(msg, text_out, reply_markup=main_menu())
    if result.approvals:
        await send_approval_buttons(update, result.approvals, "✅ Записать")


async def agent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    raw = " ".join(context.args)
    parts = raw.split(" ", 1)
    if len(parts) < 2:
        await update.message.reply_text("Формат: /agent backend сделать API")
        return
    agent, task = parts[0].lower(), parts[1]
    if agent not in AGENT_PROMPTS:
        await update.message.reply_text("Неизвестный агент. Доступны: " + ", ".join(AGENT_PROMPTS.keys()))
        return
    task_id, task_text = parse_task_ref(task)
    if task_id and not db.get_task(task_id):
        await update.message.reply_text(f"Задача #{task_id} не найдена")
        return
    msg = await update.message.reply_text(f"🤖 {agent} думает...")
    ans = ask_agent(agent, task_text, db.memories(), extra=f"task_id={task_id}" if task_id else "")
    status = result_status(agent, ans)
    db.log(f"agent:{agent}", ans)
    db.add_message("user", f"/agent {agent} {task}")
    db.add_message("assistant", ans, task_id)
    db.add_agent_result(agent, ans, task_id, status)
    if task_id and agent in {"tester", "reviewer", "security"}:
        db.set_task_status(task_id, "needs_fix" if status == "blockers" else "testing")
    await edit_or_reply_long(msg, ans, reply_markup=agent_menu())


async def tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, db.list_tasks(), reply_markup=main_menu())


async def addtask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    raw = " ".join(context.args)
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2:
        await update.message.reply_text("Формат: /addtask frontend | сверстать главную | описание")
        return
    agent, title = parts[0], parts[1]
    desc = parts[2] if len(parts) > 2 else ""
    task_id = db.add_task(title, desc, agent, status="new")
    db.add_message("user", raw, task_id)
    await update.message.reply_text(f"✅ Добавил задачу #{task_id}", reply_markup=main_menu())


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /status 3 testing")
        return
    task_id = int(context.args[0])
    status = context.args[1]
    if status not in TASK_STATUSES:
        await update.message.reply_text("Статусы: " + ", ".join(sorted(TASK_STATUSES)))
        return
    if not db.get_task(task_id):
        await update.message.reply_text(f"Задача #{task_id} не найдена")
        return
    if status == "done" and not db.has_successful_review(task_id):
        approval_id = db.create_approval(update.effective_user.id, "mark_done", {"task_id": task_id})
        await update.message.reply_text(
            f"У задачи #{task_id} нет успешного tester/reviewer результата. Подтвердить done вручную?",
            reply_markup=approval_menu(approval_id, "✅ Done"),
        )
        return
    db.set_task_status(task_id, status)
    await update.message.reply_text(f"✅ #{task_id} -> {status}", reply_markup=main_menu())


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /done 3")
        return
    task_id = int(context.args[0])
    if not db.get_task(task_id):
        await update.message.reply_text(f"Задача #{task_id} не найдена")
        return
    if db.has_successful_review(task_id):
        db.set_task_status(task_id, "done")
        await update.message.reply_text("✅ Готово", reply_markup=main_menu())
        return
    approval_id = db.create_approval(update.effective_user.id, "mark_done", {"task_id": task_id})
    await update.message.reply_text(
        f"У задачи #{task_id} нет успешного tester/reviewer результата. Подтвердить done вручную?",
        reply_markup=approval_menu(approval_id, "✅ Done"),
    )


async def approvals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, format_pending_approvals(update.effective_user.id), reply_markup=main_menu())


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    approval_id = parse_approval_id(context.args)
    if approval_id is None:
        await update.message.reply_text("Формат: /approve 12")
        return
    out = execute_approval(approval_id, update.effective_user.id)
    await reply_long(update, out, reply_markup=main_menu())


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    approval_id = parse_approval_id(context.args)
    if approval_id is None:
        await update.message.reply_text("Формат: /cancel 12")
        return
    out = cancel_approval(approval_id, update.effective_user.id)
    await reply_long(update, out, reply_markup=main_menu())


async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, db.memories(), reply_markup=main_menu())


async def remember_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text("Формат: /remember проект | webcord, стек next+prisma")
        return
    key, value = [p.strip() for p in raw.split("|", 1)]
    db.remember(key, value)
    await update.message.reply_text("💾 Запомнил", reply_markup=main_menu())


async def shell_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    command = " ".join(context.args)
    if not command:
        await update.message.reply_text("Формат: /shell git status")
        return
    check = classify_command(command)
    if not check.allowed:
        await update.message.reply_text(f"⛔ Команда не пройдёт: {check.reason}")
        return
    if check.category == READ_ONLY and AUTO_APPROVE_SAFE_COMMANDS:
        await reply_long(update, run_safe(command), reply_markup=main_menu())
        return
    approval_id = db.create_approval(update.effective_user.id, "shell", {"command": command})
    await update.message.reply_text(
        f"Подтверди команду ({check.category}):\n{command}",
        reply_markup=approval_menu(approval_id),
    )


async def gitstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, run_safe("git status"), reply_markup=main_menu())


async def gitdiff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, run_safe("git diff"), reply_markup=main_menu())


async def commit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    message = " ".join(context.args).strip()
    if not message:
        await update.message.reply_text("Формат: /commit сообщение")
        return
    if any(part in message for part in ("&", "|", ";", "<", ">", "\n", "\r", "`", '"')):
        await update.message.reply_text("Сообщение commit содержит запрещённые символы")
        return
    diff = run_safe("git diff")
    command = f'git commit -m "{message}"'
    check = classify_command(command)
    if not check.allowed:
        await update.message.reply_text(f"⛔ Commit не пройдёт: {check.reason}")
        return
    approval_id = db.create_approval(update.effective_user.id, "shell", {"command": command})
    await reply_long(
        update,
        f"Подтверди commit:\n{command}\n\nDiff preview:\n{diff}",
        reply_markup=approval_menu(approval_id, "✅ Commit"),
    )


async def steam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Формат: /steam 730")
        return
    app_id = "".join(ch for ch in context.args[0] if ch.isdigit())
    if not app_id:
        await update.message.reply_text("Нужен числовой Steam app_id")
        return
    approval_id = db.create_approval(update.effective_user.id, "steam", {"app_id": app_id})
    await update.message.reply_text(
        f"Подтверди установку Steam app_id={app_id}",
        reply_markup=approval_menu(approval_id, "✅ Открыть Steam install"),
    )


async def steamstart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    games = db.list_steam_games()
    if not games:
        await update.message.reply_text("Steam games list is empty.", reply_markup=main_menu())
        return
    await update.message.reply_text("Choose a Steam game to launch:", reply_markup=steam_games_menu())


async def pc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    raw = " ".join(context.args) or update.message.text.replace("/pc", "", 1).strip()
    if not raw:
        await update.message.reply_text(
            "Напиши: /pc включи парадеевича на ютубе\n"
            "Можно также: /pc открой ютуб, /pc найди python на ютубе, /pc https://example.com",
            reply_markup=pc_shortcuts_menu(),
        )
        return
    try:
        target = resolve_pc_request(raw, db.list_pc_shortcuts())
        if not target:
            await update.message.reply_text("Не понял, что открыть. Попробуй: /pc открой ютуб", reply_markup=pc_shortcuts_menu())
            return
        out = open_pc_target(target)
        db.log("pc:open", f"user_id={update.effective_user.id} source={target.source} name={target.name}\n{target.url}\n{out}")
        await update.message.reply_text(out, reply_markup=main_menu())
    except Exception as exc:
        db.log("pc:error", f"request={raw}\n{exc!r}")
        await update.message.reply_text(f"Не удалось открыть: {exc}", reply_markup=main_menu())


async def voice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    voice = update.message.voice if update.message else None
    if not voice:
        return
    if voice.duration and voice.duration > MAX_VOICE_SECONDS:
        await update.message.reply_text(f"Голосовое длиннее {MAX_VOICE_SECONDS} секунд пока не принимаю.")
        return

    msg = await update.message.reply_text("Слушаю голосовое...")
    temp_path = None
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(prefix="jarvis_voice_", suffix=".ogg", delete=False) as tmp:
            temp_path = Path(tmp.name)
        await tg_file.download_to_drive(custom_path=str(temp_path))

        text = transcribe_audio(temp_path)
        if not text:
            await edit_or_reply_long(msg, "Не смог разобрать голосовое.", reply_markup=main_menu())
            return

        target = resolve_pc_request(text, db.list_pc_shortcuts(), allow_fallback_search=False)
        if target:
            out = open_pc_target(target)
            db.log("voice:pc", f"user_id={update.effective_user.id} text={text}\n{target.url}\n{out}")
            await edit_or_reply_long(msg, f"Распознал: {text}\n\n{out}", reply_markup=main_menu())
            return

        db.log("voice:transcribed", f"user_id={update.effective_user.id} text={text}")
        await edit_or_reply_long(
            msg,
            "Распознал голосовое:\n"
            f"{text}\n\n"
            "Пока голосом выполняю PC-команды вроде: включи парадеевича на ютубе.",
            reply_markup=main_menu(),
        )
    except SpeechQuotaError:
        text = (
            "Голосовое распознавание сейчас недоступно: закончилась квота OpenAI API.\n\n"
            "Пока можно писать команду текстом, например:\n"
            "/pc включи парадеевича на ютубе"
        )
        db.log("voice:quota", f"user_id={update.effective_user.id} file_id={voice.file_id}")
        await edit_or_reply_long(msg, text, reply_markup=main_menu())
    except Exception as exc:
        db.log("voice:error", repr(exc))
        await edit_or_reply_long(
            msg,
            "Не удалось обработать голосовое. Попробуй еще раз или напиши команду текстом.",
            reply_markup=main_menu(),
        )
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await reply_long(update, list_files(), reply_markup=main_menu())


async def read_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    rel = " ".join(context.args)
    if not rel:
        await update.message.reply_text("Формат: /read path/to/file")
        return
    try:
        await reply_long(update, read_file(rel), reply_markup=main_menu())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def write_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text("Формат: /write path/to/file.txt | новый текст")
        return
    rel, content = [part.strip() for part in raw.split("|", 1)]
    if not rel:
        await update.message.reply_text("Укажи путь внутри workspace")
        return
    try:
        diff = preview_diff(rel, content)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return
    approval_id = db.create_approval(update.effective_user.id, "write_file", {"path": rel, "content": content})
    await reply_long(
        update,
        f"Подтверди запись файла: {rel}\n\n{diff}",
        reply_markup=approval_menu(approval_id, "✅ Записать файл"),
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    if not await guard(update):
        await answer_callback(q, "Нет доступа")
        return

    data = str(q.data or "")
    await answer_callback(q, "Принял")
    db.log("callback", f"user_id={update.effective_user.id if update.effective_user else '?'} data={data}")
    try:
        if data == "menu:home":
            await query_result(q, "Главное меню:", reply_markup=main_menu())
        elif data == "menu:agents":
            await query_result(q, "Выбери агента. Потом пиши: /agent frontend твоя задача", reply_markup=agent_menu())
        elif data == "menu:tasks":
            await query_result(q, db.list_tasks(), reply_markup=main_menu())
        elif data == "menu:approvals":
            await query_result(q, format_pending_approvals(update.effective_user.id), reply_markup=main_menu())
        elif data == "menu:files":
            await query_result(q, list_files(), reply_markup=main_menu())
        elif data == "menu:memory":
            await query_result(q, db.memories(), reply_markup=main_menu())
        elif data == "menu:plan":
            await query_result(q, "Напиши: /plan твоя задача", reply_markup=main_menu())
        elif data == "menu:build":
            await query_result(q, "Напиши: /build сделать лендинг для ЦТТ Новация", reply_markup=main_menu())
        elif data == "menu:shell":
            await query_result(q, "Напиши: /shell команда\nНапример: /shell git status", reply_markup=main_menu())
        elif data == "menu:steam":
            await query_result(q, "Choose a Steam game to launch, or use /steam app_id to open install:", reply_markup=steam_games_menu())
        elif data == "menu:pc":
            await query_result(
                q,
                "Напиши: /pc включи парадеевича на ютубе\nИли выбери готовый shortcut:",
                reply_markup=pc_shortcuts_menu(),
            )
        elif data == "menu:security":
            await query_result(q, "Напиши: /agent security что проверить", reply_markup=main_menu())
        elif data.startswith("agent:"):
            ag = data.split(":", 1)[1]
            await query_result(q, f"Ок. Теперь: /agent {ag} твоя задача", reply_markup=agent_menu())
        elif data.startswith("steamstart:"):
            app_id = data.split(":", 1)[1]
            game = db.get_steam_game(app_id)
            if not game:
                await query_result(q, f"Steam game app_id={app_id} not found.", reply_markup=steam_games_menu())
                return
            out = launch_steam_game(game[0], game[1])
            db.log("steam:start", f"user_id={update.effective_user.id} app_id={game[0]} name={game[1]}\n{out}")
            await query_result(q, out, reply_markup=steam_games_menu())
        elif data.startswith("pcopen:"):
            slug = data.split(":", 1)[1]
            item = db.get_pc_shortcut(slug)
            if not item:
                await query_result(q, f"PC shortcut {slug} not found.", reply_markup=pc_shortcuts_menu())
                return
            target = resolve_pc_request(item["name"], [item])
            if not target:
                await query_result(q, f"Could not resolve PC shortcut {slug}.", reply_markup=pc_shortcuts_menu())
                return
            out = open_pc_target(target)
            db.log("pc:open", f"user_id={update.effective_user.id} slug={slug} name={target.name}\n{target.url}\n{out}")
            await query_result(q, out, reply_markup=pc_shortcuts_menu())
        elif data.startswith("confirm:"):
            approval_id = int(data.split(":", 1)[1])
            out = execute_approval(approval_id, update.effective_user.id)
            await query_result(q, out, reply_markup=main_menu())
        elif data.startswith("cancel:"):
            approval_id = int(data.split(":", 1)[1])
            out = cancel_approval(approval_id, update.effective_user.id)
            await query_result(q, out, reply_markup=main_menu())
        else:
            await query_result(q, f"Неизвестная кнопка: {data}", reply_markup=main_menu())
    except Exception as exc:
        db.log("callback:error", f"data={data}\n{exc!r}")
        print(f"Telegram callback failed for {data}: {exc!r}")
        try:
            await answer_callback(q, "Ошибка кнопки")
            await query_result(q, f"Ошибка кнопки: {exc}", reply_markup=main_menu())
        except Exception:
            pass


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = update.message.text
    is_new_task, cleaned_text = strip_new_task_prefix(text)
    if is_new_task:
        db.cancel_pending_clarification(update.effective_user.id)
        text = cleaned_text
        if not text:
            await update.message.reply_text("После `новая задача:` напиши текст задачи.")
            return

    pending = None if is_new_task else db.get_pending_clarification(update.effective_user.id)
    if pending:
        db.resolve_clarification(pending["id"])
        combined = build_clarified_task(pending["task_text"], text)
        if pending["mode"] in {"run", "build"}:
            if pending["mode"] == "build":
                msg = await update.message.reply_text("🏗 Принял уточнение, готовлю файлы...")
                result = orchestrator.build_task(combined, update.effective_user.id)
            else:
                msg = await update.message.reply_text("🧭 Принял уточнение, обновляю JSON-план...")
                result = orchestrator.plan_task(combined, update.effective_user.id)
            pending_note = ""
            if result.needs_clarification:
                remember_clarification(update.effective_user.id, pending["mode"], combined, result.text, result.questions or None)
                pending_note = "Жду ещё одно уточнение обычным сообщением. Для новой задачи начни с: новая задача:"
            elif pending["mode"] == "build" and result.approvals:
                pending_note = "После approve файлы появятся в workspace. Посмотреть их можно через /files."
            text_out = format_orchestration_result(result, pending_note)
            await edit_or_reply_long(msg, text_out, reply_markup=main_menu())
            if pending["mode"] == "build" and result.approvals:
                await send_approval_buttons(update, result.approvals, "✅ Записать")
            return

        db.add_message("user", combined)
        msg = await update.message.reply_text("🧠 Принял уточнение, продолжаю прошлую задачу...")
        ans = build_plan(combined, db.memories())
        if assistant_needs_clarification(ans):
            remember_clarification(update.effective_user.id, "plan", combined, ans)
            ans += clarification_hint()
        db.log("clarification", combined)
        db.log("answer", ans)
        db.add_message("assistant", ans)
        await edit_or_reply_long(msg, ans, reply_markup=main_menu())
        return

    db.add_message("user", text)
    msg = await update.message.reply_text("🧠 Разбираю задачу...")
    ans = build_plan(text, db.memories())
    if assistant_needs_clarification(ans):
        remember_clarification(update.effective_user.id, "plan", text, ans)
        ans += clarification_hint()
    db.log("incoming", text)
    db.log("answer", ans)
    db.add_message("assistant", ans)
    await edit_or_reply_long(msg, ans, reply_markup=main_menu())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Telegram handler error: {context.error!r}")
    if isinstance(update, Update) and update.effective_message:
        try:
            await telegram_call("error reply", update.effective_message.reply_text, f"Ошибка: {context.error}", retries=1)
        except Exception:
            pass


def main():
    validate_bot_config()
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plan", plan_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("build", build_cmd))
    app.add_handler(CommandHandler("agent", agent_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("addtask", addtask_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("approvals", approvals_cmd))
    app.add_handler(CommandHandler("approve", approve_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("remember", remember_cmd))
    app.add_handler(CommandHandler("shell", shell_cmd))
    app.add_handler(CommandHandler("gitstatus", gitstatus_cmd))
    app.add_handler(CommandHandler("gitdiff", gitdiff_cmd))
    app.add_handler(CommandHandler("commit", commit_cmd))
    app.add_handler(CommandHandler("steam", steam_cmd))
    app.add_handler(CommandHandler("steamstart", steamstart_cmd))
    app.add_handler(CommandHandler("pc", pc_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("read", read_cmd))
    app.add_handler(CommandHandler("write", write_cmd))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.VOICE, voice_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))
    app.add_error_handler(error_handler)
    print("Jarvis MVP+ started")
    app.run_polling()


if __name__ == "__main__":
    main()
