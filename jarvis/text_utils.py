import re


def chunks(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return [""]
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def parse_task_ref(text: str) -> tuple[int | None, str]:
    match = re.match(r"^\s*#(\d+)\s+(.+)$", text, re.S)
    if not match:
        return None, text
    return int(match.group(1)), match.group(2).strip()
