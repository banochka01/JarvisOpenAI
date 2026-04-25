from openai import OpenAI

from jarvis.config import OPENAI_API_KEY, OPENAI_MODEL, validate_openai_config
from jarvis.agents.prompts import AGENT_PROMPTS


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    validate_openai_config()
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY, timeout=60)
    return _client


def _call_openai(system: str, content: str) -> str:
    sdk = client()
    if hasattr(sdk, "responses"):
        resp = sdk.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return resp.output_text

    resp = sdk.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    )
    message = resp.choices[0].message
    return message.content or ""


def ask_agent(agent: str, user_text: str, memory: str = "", extra: str = "") -> str:
    system = AGENT_PROMPTS.get(agent, AGENT_PROMPTS["supervisor"])
    content = f"""Память/контекст:
{memory}

Дополнительно:
{extra}

Задача пользователя:
{user_text}"""
    try:
        return _call_openai(system, content)
    except Exception as exc:
        return f"Ошибка OpenAI: {exc}"


def build_plan(user_text: str, memory: str = "") -> str:
    prompt = f"""Сделай безопасный MVP-план в формате:
1. Нужно ли уточнение? да/нет. Если да, задай 1-3 конкретных вопроса и не выдумывай детали.
2. Подзадачи по агентам: backend, frontend, tester, devops, security, reviewer.
3. Порядок цикла: plan -> execute -> test -> review -> fix/retest -> done.
4. Риски и команды/записи файлов, которые требуют Telegram approve/cancel.

Важно:
- Не проси выполнить опасные команды без подтверждения.
- Не предлагай доступ к C:\\Users, AppData, браузерным кукам, токенам и неизвестным exe.
- Любые изменения файлов сначала должны идти как diff/preview.
- Если нужны уточнения, остановись после вопросов и не строй план до ответа пользователя.

Задача: {user_text}"""
    return ask_agent("supervisor", prompt, memory)


def build_action_plan(user_text: str, memory: str = "") -> str:
    prompt = f"""Верни только JSON object без Markdown.

Schema:
{{
  "needs_clarification": false,
  "questions": [],
  "summary": "краткое резюме",
  "plan": ["шаг 1", "шаг 2"],
  "proposed_actions": [
    {{"type": "shell", "command": "git status"}},
    {{"type": "write_file", "path": "relative/path.txt", "content": "text"}},
    {{"type": "steam", "app_id": "730"}}
  ]
}}

Правила:
- proposed_actions можно оставлять пустым, если безопаснее сначала уточнить.
- Не добавляй опасные команды, shell operators, абсолютные пути, доступ к C:\\Users/AppData/cookies/tokens.
- Любая запись файла должна быть полным content для файла, а не инструкцией словами.
- Если задача мутная, поставь needs_clarification=true и задай 1-3 questions.
- Если needs_clarification=true, proposed_actions должен быть пустым и plan должен содержать только шаг ожидания ответа.

Память:
{memory}

Задача: {user_text}"""
    return ask_agent("supervisor", prompt, memory)


def build_site_action_plan(user_text: str, memory: str = "") -> str:
    prompt = f"""Верни только JSON object без Markdown.

Ты builder-агент. Твоя задача — не просто спланировать, а подготовить готовые файлы для статического сайта/страницы внутри workspace.

Schema:
{{
  "needs_clarification": false,
  "questions": [],
  "summary": "что будет создано",
  "plan": ["какие файлы будут записаны"],
  "proposed_actions": [
    {{"type": "write_file", "path": "site/index.html", "content": "полный HTML"}},
    {{"type": "write_file", "path": "site/styles.css", "content": "полный CSS"}},
    {{"type": "write_file", "path": "site/script.js", "content": "полный JS, если нужен"}}
  ]
}}

Правила:
- Для сайта/лендинга/заглушки почти всегда создавай минимум index.html и styles.css с полным содержимым.
- Используй относительные пути внутри workspace. Хороший путь: site/index.html или landing/index.html.
- Не используй внешние CDN, трекеры, analytics, формы с реальной отправкой данных или серверные интеграции.
- Если пользователь дал телефон, email или текст, можно показать их статически на странице.
- Если часть деталей неизвестна, сделай разумные безопасные допущения и укажи их в summary.
- Ставь needs_clarification=true только если без ответа невозможно сделать полезный результат.
- Если needs_clarification=true, proposed_actions должен быть пустым.
- Не добавляй shell actions для build-режима.

Память:
{memory}

Задача: {user_text}"""
    return ask_agent("supervisor", prompt, memory)
