# Jarvis Agents MVP+

Telegram-бот-оркестратор для Windows 10: пользователь пишет задачу в Telegram, supervisor через OpenAI API планирует работу, задаёт уточнения и делегирует ролям `backend`, `frontend`, `tester`, `devops`, `security`, `reviewer`.

Главный принцип MVP: бот помогает планировать и запускать ограниченные dev-команды внутри `workspace`, но не получает полный доступ к ПК.

## Установка На Windows 10

1. Установи зависимости:
   - Python 3.12.x с галочкой **Add Python to PATH** и py launcher: https://www.python.org/downloads/release/python-3128/
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

Важно: не используй Python 3.14 для этого MVP. На Windows часть зависимостей может начать собираться из исходников через Rust/maturin. Установщик специально ищет Python 3.12 и ставит только готовые binary wheels.

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

7. Запусти desktop-панель или только бота:

```bat
run_desktop_windows.bat
```

В панели можно редактировать `.env`, запускать/останавливать Telegram-бота, смотреть задачи, approvals и логи. Если нужен только Telegram-бот без окна:

```bat
run_windows.bat
```

## Telegram Команды

- `/start` — главное меню с кнопками.
- `/help` — список команд.
- `/plan текст` — безопасный план задачи.
- `/run текст` — создать задачу, получить JSON-план supervisor и завести approvals на proposed actions.
- `/build текст` — создать готовые файлы статического сайта/страницы через `write_file` approvals.
- `/agent backend|frontend|tester|devops|reviewer|security текст` — спросить агента.
- `/agent reviewer #3 текст` — записать результат reviewer/tester/security для задачи `#3`.
- `/tasks` — список задач.
- `/addtask агент | название | описание` — создать задачу со статусом `new`.
- `/status id статус` — поставить статус вручную.
- `/done id` — отметить задачу выполненной после успешного tester/reviewer результата или через approve.
- `/approvals` — показать ожидающие подтверждения.
- `/approve id` — выполнить approval без кнопки.
- `/cancel id` — отменить approval без кнопки.
- `/shell команда` — выполнить whitelisted-команду внутри workspace.
- `/gitstatus` — показать `git status`.
- `/gitdiff` — показать `git diff`.
- `/commit сообщение` — показать diff и создать approve на `git commit`.
- `/steam app_id` — открыть `steam://install/app_id` после approve.
- `/steamstart` — выбрать игру кнопкой и запустить через `steam://rungameid/app_id`.
- `/pc запрос` — открыть сайт или приложение, например `/pc включи парадеевича на ютубе`, `/pc открой яндекс музыку`, `/pc запусти валик`.
- голосовое сообщение в Telegram — распознать речь и выполнить PC-команду, если фраза понятна.
- `/files` — список файлов workspace.
- `/read путь` — прочитать файл workspace.
- `/write путь | текст` — показать diff и записать файл после approve.
- `/remember ключ | значение` и `/memory` — простая память.

Статусы задач: `new`, `planned`, `in_progress`, `testing`, `needs_fix`, `done`, `failed`.

Локальные приложения для `/pc` можно привязать к `.lnk` или `.exe` через `.env`:

```env
PC_APP_YANDEX_MUSIC_PATH=C:\path\to\Yandex Music.lnk
PC_APP_SPOTIFY_PATH=C:\path\to\Spotify.lnk
PC_APP_VALORANT_PATH=C:\path\to\VALORANT.lnk
PC_APP_AYUGRAM_PATH=C:\path\to\AyuGram.exe
```

Если путь не задан, Yandex Music и Spotify откроются в браузере, VALORANT попробует стартовать через Riot URI, а AyuGram попросит указать путь.

## Безопасность

Jarvis работает только внутри `WORKSPACE`. Пути из `.env` должны оставаться внутри папки проекта.

Перед публикацией или пушем проверь:
- `.env`, `.venv`, SQLite-базы, архивы и содержимое `jarvis/workspace/` не должны быть в git;
- в README и `.env.example` должны оставаться только placeholders;
- Control Hub не нужно запускать на внешнем интерфейсе. Если всё же нужен remote-bind, он требует явный флаг `--allow-remote`.

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

В текущем MVP есть два режима:
- `/plan` — человекочитаемый план без создания actions.
- `/run` — supervisor возвращает JSON-протокол: `needs_clarification`, `questions`, `summary`, `plan`, `proposed_actions`.
- `/build` — builder-режим для сайтов/страниц: просит модель вернуть готовые `write_file` actions с полным HTML/CSS/JS и создаёт approvals на запись файлов.

`proposed_actions` поддерживает только:
- `shell` с командой из whitelist;
- `write_file` с относительным путём и полным content;
- `steam` с числовым `app_id`.

Все actions проходят локальную проверку. Запрещённые actions отклоняются и пишутся как security blocker. Разрешённые risky actions сохраняются в SQLite как approvals и ждут approve/cancel в Telegram или desktop-панели.

Tester/reviewer/security пишут результат в SQLite; если ответ начинается с `BLOCKERS:`, задача переходит в `needs_fix`.

## Desktop-Панель

`run_desktop_windows.bat` открывает браузерный Jarvis OpenAI Control Hub на локальном адресе `http://127.0.0.1:8765/`:
- **Overview** повторяет темный Jarvis-dashboard: hero, метрики, task queue, system modules, activity monitor и быстрые действия;
- **Tasks** показывает задачи из SQLite и текущий прогресс по статусам;
- **Systems** показывает голосовые PC shortcuts, сайты и локальные приложения;
- **Approvals** показывает pending/approved/cancelled действия, payload и кнопки approve/cancel;
- **Settings** редактирует актуальные переменные `.env`: Telegram, OpenAI, STT/VPS, proxy и пути к приложениям;
- **Theme** переключает светлую/темную тему, пресеты и ручные цвета интерфейса;
- **Logs** показывает последние события из SQLite;
- кнопки **Launch Core / Stop Core / Restart bot core** управляют Telegram-ботом из текущей venv.

Control Hub по умолчанию слушает только localhost и защищает API одноразовым session-token внутри открытой страницы. Реальные значения секретов в `/api/state` не отдаются: заполненные токены и ключи показываются как `********` и сохраняются без перезаписи, если поле не менять.

## Данные

SQLite хранит:
- задачи и статусы;
- историю сообщений;
- результаты агентов;
- approvals;
- память;
- служебные логи.

База по умолчанию: `jarvis\storage\jarvis.db`.

## Speech-to-text через VPS

По умолчанию голосовые расшифровываются через OpenAI `whisper-1`. Если квота OpenAI недоступна, можно вынести STT на VPS:

```env
STT_PROVIDER=server
STT_SERVER_URL=https://your-domain.example/transcribe
STT_SERVER_TOKEN=change-this-token
STT_SERVER_TIMEOUT=120
```

Минимальный сервер для VPS лежит в `vps_stt_server/`. Он принимает Telegram `.ogg`, распознает через `faster-whisper` и возвращает JSON `{"text": "..."}`.

## Проверка

После установки можно запустить:

```bat
pytest
```

Ручная проверка:
- `/start`
- `/plan сделать простое API`
- `/run проверить проект и предложить безопасные действия`
- `/shell git status`
- `/shell git status & del /s *` должен быть запрещён
- `/addtask reviewer | проверить безопасность | shell whitelist`
- `/agent reviewer #1 OK: критичных проблем нет`
- `/done 1`
- `run_desktop_windows.bat` должен открыть панель, показать задачи и approvals
