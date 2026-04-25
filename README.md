# JarvisOpenAI
Локально работающий джарвис на вашем пк, с подключением телеграмм бота и api open ai
# Jarvis Agents MVP+

Telegram-бот-оркестратор для Windows 10: пользователь пишет задачу в Telegram, supervisor через OpenAI API планирует работу, задаёт уточнения и делегирует ролям `backend`, `frontend`, `tester`, `devops`, `security`, `reviewer`.

Главный принцип MVP: бот помогает планировать и запускать ограниченные dev-команды внутри `workspace`, но не получает полный доступ к ПК.

## Установка На Windows 10

1. Установи зависимости:
   - Python 3.12+ с галочкой **Add Python to PATH**: https://www.python.org/downloads/windows/
   - Git for Windows: https://git-scm.com/download/win
   - Node.js LTS: https://nodejs.org/
   - Docker Desktop, если нужны Docker-команды: https://www.docker.com/products/docker-desktop/

2. Распакуй проект в удобную папку, например:

```bat
C:\Jarvis\jarvis_agents_mvp_plus
```

3. Создай Telegram-бота:
   - открой Telegram;
   - напиши `@BotFather`;
   - выполни `/newbot`;
   - задай имя и username;
   - скопируй `TELEGRAM_BOT_TOKEN`.

4. Узнай свой Telegram user_id:
   - напиши `@userinfobot`;
   - скопируй числовой `Id`.

5. Установи Python-зависимости:

```bat
install_windows.bat
```

6. Открой `.env` и заполни:

```env
TELEGRAM_BOT_TOKEN=токен от BotFather
ALLOWED_USER_ID=твой Telegram id
OPENAI_API_KEY=твой OpenAI API key
OPENAI_MODEL=gpt-4.1-mini
WORKSPACE=jarvis\workspace
DB_PATH=jarvis\storage\jarvis.db
AUTO_APPROVE_SAFE_COMMANDS=0
```

7. Запусти бота:

```bat
run_windows.bat
```

## Telegram Команды

- `/start` — главное меню с кнопками.
- `/help` — список команд.
- `/plan текст` — безопасный план задачи.
- `/agent backend|frontend|tester|devops|reviewer|security текст` — спросить агента.
- `/agent reviewer #3 текст` — записать результат reviewer/tester/security для задачи `#3`.
- `/tasks` — список задач.
- `/addtask агент | название | описание` — создать задачу со статусом `new`.
- `/status id статус` — поставить статус вручную.
- `/done id` — отметить задачу выполненной после успешного tester/reviewer результата или через approve.
- `/shell команда` — выполнить whitelisted-команду внутри workspace.
- `/steam app_id` — открыть `steam://install/app_id` после approve.
- `/files` — список файлов workspace.
- `/read путь` — прочитать файл workspace.
- `/write путь | текст` — показать diff и записать файл после approve.
- `/remember ключ | значение` и `/memory` — простая память.

Статусы задач: `new`, `planned`, `in_progress`, `testing`, `needs_fix`, `done`, `failed`.

## Безопасность

Jarvis работает только внутри `WORKSPACE`. Пути из `.env` должны оставаться внутри папки проекта.

Запрещено:
- `del`, `rmdir`, `format`, `shutdown`, `reg`;
- shell chaining: `&`, `&&`, `|`, `;`, redirects `<` и `>`;
- `powershell -enc`, `curl | powershell`, `wget | powershell`;
- доступ к `C:\Users`, `AppData`, browser cookies, tokens, credentials;
- запуск неизвестных `.exe` и команд вне whitelist.

Read-only команды могут выполняться без кнопки только если `AUTO_APPROVE_SAFE_COMMANDS=1`:
- `git status`
- `git diff`
- `git log`
- безопасный `git branch`
- `dir`, `ls`, `cat`, `type` внутри workspace

Risky-команды всегда требуют Telegram approve/cancel:
- `git add`, `git commit`
- `npm install`, `npm run`, `npx`, `node`
- `python`, `py`
- `docker compose`
- `steam install`
- запись файлов

## Agent Loop MVP

Целевой цикл:

```text
task -> plan -> execute -> test -> review -> fix -> retest -> done
```

В текущем MVP агенты сначала дают план, diff/preview или команды. Бот выполняет только разрешённые tools и только после approve, если действие рискованное. Tester/reviewer/security пишут результат в SQLite; если ответ начинается с `BLOCKERS:`, задача переходит в `needs_fix`.

## Данные

SQLite хранит:
- задачи и статусы;
- историю сообщений;
- результаты агентов;
- approvals;
- память;
- служебные логи.

База по умолчанию: `jarvis\storage\jarvis.db`.

## Проверка

После установки можно запустить:

```bat
pytest
```

Ручная проверка:
- `/start`
- `/plan сделать простое API`
- `/shell git status`
- `/shell git status & del /s *` должен быть запрещён
- `/addtask reviewer | проверить безопасность | shell whitelist`
- `/agent reviewer #1 OK: критичных проблем нет`
- `/done 1`
