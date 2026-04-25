import asyncio

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

from jarvis.agents.llm import ask_agent, build_plan
from jarvis.agents.prompts import AGENT_PROMPTS
from jarvis.config import ALLOWED_USER_ID, AUTO_APPROVE_SAFE_COMMANDS, DB_PATH, TELEGRAM_BOT_TOKEN, validate_bot_config
from jarvis.orchestrator import Orchestrator
from jarvis.storage.db import TASK_STATUSES, JarvisDB
from jarvis.text_utils import chunks, parse_task_ref
from jarvis.tools.file_tool import list_files, preview_diff, read_file, write_file
from jarvis.tools.safe_shell import READ_ONLY, classify_command, run_safe
from jarvis.tools.steam_tool import install_steam_game


MAX_TG_TEXT = 3900
db = JarvisDB(DB_PATH)
orchestrator = Orchestrator(db)


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 План", callback_data="menu:plan"), InlineKeyboardButton("🧩 Агенты", callback_data="menu:agents")],
        [InlineKeyboardButton("📋 Задачи", callback_data="menu:tasks"), InlineKeyboardButton("🗂 Файлы", callback_data="menu:files")],
        [InlineKeyboardButton("🛡 Проверка", callback_data="menu:security"), InlineKeyboardButton("💾 Память", callback_data="menu:memory")],
        [InlineKeyboardButton("⚙️ Shell", callback_data="menu:shell"), InlineKeyboardButton("🎮 Steam", callback_data="menu:steam")],
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


async def answer_callback(query) -> None:
    try:
        await telegram_call("answer callback", query.answer, retries=2)
    except Exception as exc:
        print(f"Telegram callback answer failed, continuing: {exc!r}")


async def reply_long(update: Update, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    for part in parts[:-1]:
        await telegram_call("reply text", update.effective_message.reply_text, part)
    await telegram_call("reply text", update.effective_message.reply_text, parts[-1], reply_markup=reply_markup)


async def edit_or_reply_long(message, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    await telegram_call("edit text", message.edit_text, parts[0], reply_markup=reply_markup if len(parts) == 1 else None)
    for part in parts[1:-1]:
        await telegram_call("reply text", message.reply_text, part)
    if len(parts) > 1:
        await telegram_call("reply text", message.reply_text, parts[-1], reply_markup=reply_markup)


async def query_result(query, text: str, reply_markup=None):
    parts = chunks(text, MAX_TG_TEXT)
    await telegram_call("edit callback message", query.edit_message_text, parts[0], reply_markup=reply_markup if len(parts) == 1 else None)
    for part in parts[1:-1]:
        await telegram_call("reply callback text", query.message.reply_text, part)
    if len(parts) > 1:
        await telegram_call("reply callback text", query.message.reply_text, parts[-1], reply_markup=reply_markup)


def result_status(agent: str, answer: str) -> str:
    upper = answer.strip().upper()
    if agent in {"tester", "reviewer", "security"} and upper.startswith("BLOCKERS:"):
        return "blockers"
    return "ok"


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
/agent backend|frontend|tester|devops|reviewer|security текст — спросить агента
/agent reviewer #3 текст — reviewer/tester результат для задачи #3
/tasks — список задач
/addtask агент | название | описание — добавить задачу
/status id статус — поставить new/planned/in_progress/testing/needs_fix/done/failed
/done id — отметить задачу done после review/test или через approve
/shell команда — команда в workspace через whitelist и approve
/gitstatus — показать git status
/gitdiff — показать git diff
/commit сообщение — создать approval на git commit
/steam app_id — открыть установку Steam через approve
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
    db.add_message("user", text)
    msg = await update.message.reply_text("🧠 Думаю план...")
    ans = build_plan(text, db.memories())
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
    msg = await update.message.reply_text("🧭 Собираю JSON-план и approvals...")
    result = orchestrator.plan_task(text, update.effective_user.id)
    extra = []
    if result.approvals:
        extra.append("Approvals созданы: " + ", ".join(f"#{item}" for item in result.approvals))
    if result.rejected_actions:
        extra.append("Отклонённые actions: " + str(len(result.rejected_actions)))
    text_out = f"Задача #{result.task_id}\n\n{result.text}"
    if extra:
        text_out += "\n\n" + "\n".join(extra)
    await edit_or_reply_long(msg, text_out, reply_markup=main_menu())


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
    if not await guard(update):
        return
    q = update.callback_query
    await answer_callback(q)
    data = q.data
    if data == "menu:home":
        await query_result(q, "Главное меню:", reply_markup=main_menu())
    elif data == "menu:agents":
        await query_result(q, "Выбери агента. Потом пиши: /agent frontend твоя задача", reply_markup=agent_menu())
    elif data == "menu:tasks":
        await query_result(q, db.list_tasks(), reply_markup=main_menu())
    elif data == "menu:files":
        await query_result(q, list_files(), reply_markup=main_menu())
    elif data == "menu:memory":
        await query_result(q, db.memories(), reply_markup=main_menu())
    elif data == "menu:plan":
        await query_result(q, "Напиши: /plan твоя задача", reply_markup=main_menu())
    elif data == "menu:shell":
        await query_result(q, "Напиши: /shell команда\nНапример: /shell git status", reply_markup=main_menu())
    elif data == "menu:steam":
        await query_result(q, "Напиши: /steam app_id\nНапример: /steam 730", reply_markup=main_menu())
    elif data == "menu:security":
        await query_result(q, "Напиши: /agent security что проверить", reply_markup=main_menu())
    elif data.startswith("agent:"):
        ag = data.split(":", 1)[1]
        await query_result(q, f"Ок. Теперь: /agent {ag} твоя задача", reply_markup=agent_menu())
    elif data.startswith("confirm:"):
        approval_id = int(data.split(":", 1)[1])
        action = db.get_pending_approval(approval_id, update.effective_user.id)
        if not action:
            await query_result(q, "Действие уже не найдено/устарело.")
            return
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
        await query_result(q, out, reply_markup=main_menu())
    elif data.startswith("cancel:"):
        approval_id = int(data.split(":", 1)[1])
        db.decide_approval(approval_id, "cancelled")
        await query_result(q, "❌ Отменено", reply_markup=main_menu())


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = update.message.text
    db.add_message("user", text)
    msg = await update.message.reply_text("🧠 Разбираю задачу...")
    ans = build_plan(text, db.memories())
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
    app.add_handler(CommandHandler("agent", agent_cmd))
    app.add_handler(CommandHandler("tasks", tasks_cmd))
    app.add_handler(CommandHandler("addtask", addtask_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("remember", remember_cmd))
    app.add_handler(CommandHandler("shell", shell_cmd))
    app.add_handler(CommandHandler("gitstatus", gitstatus_cmd))
    app.add_handler(CommandHandler("gitdiff", gitdiff_cmd))
    app.add_handler(CommandHandler("commit", commit_cmd))
    app.add_handler(CommandHandler("steam", steam_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("read", read_cmd))
    app.add_handler(CommandHandler("write", write_cmd))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))
    app.add_error_handler(error_handler)
    print("Jarvis MVP+ started")
    app.run_polling()


if __name__ == "__main__":
    main()
