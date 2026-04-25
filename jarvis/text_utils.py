import re


NEW_TASK_PREFIXES = ("новая задача:", "новый запрос:", "new task:")


def chunks(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def parse_task_ref(text: str) -> tuple[int | None, str]:
    match = re.match(r"^\s*#(\d+)\s+(.+)$", text, re.S)
    if not match:
        return None, text
    return int(match.group(1)), match.group(2).strip()


def strip_new_task_prefix(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    lower = stripped.lower()
    for prefix in NEW_TASK_PREFIXES:
        if lower.startswith(prefix):
            return True, stripped[len(prefix):].strip()
    return False, text


def assistant_needs_clarification(text: str) -> bool:
    lower = text.lower()
    if re.search(r"нужно ли уточнение\?\s*(да|yes)", lower):
        return True
    if re.search(r'"needs_clarification"\s*:\s*true', lower):
        return True
    if "уточнения:" in lower:
        return True
    return "пока не хватает контекста" in lower


def extract_clarification_questions(text: str) -> list[str]:
    questions = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -*\t")
        if not line:
            continue
        if "?" in line and "нужно ли уточнение" not in line.lower():
            questions.append(line)
    return questions[:3] or [text.strip()]


def build_clarified_task(original_text: str, clarification_text: str) -> str:
    return f"""Продолжи предыдущую задачу с учётом ответа пользователя.

Исходная задача:
{original_text}

Уточнение пользователя:
{clarification_text}

Собери обновлённый план по этой же задаче. Не считай уточнение новой отдельной задачей."""
