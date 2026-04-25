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


def ask_agent(agent: str, user_text: str, memory: str = "", extra: str = "") -> str:
    system = AGENT_PROMPTS.get(agent, AGENT_PROMPTS["supervisor"])
    content = f"""Память/контекст:
{memory}

Дополнительно:
{extra}

Задача пользователя:
{user_text}"""
    try:
        resp = client().responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return resp.output_text
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

Память:
{memory}

Задача: {user_text}"""
    return ask_agent("supervisor", prompt, memory)
