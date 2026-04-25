import difflib
from pathlib import Path

from jarvis.config import WORKSPACE


def safe_path(rel: str) -> Path:
    root = WORKSPACE.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Выход за workspace запрещён") from exc
    return target


def preview_diff(rel: str, content: str) -> str:
    p = safe_path(rel)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    old_lines = old.splitlines(keepends=True)
    new_lines = content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        lineterm="",
    )
    text = "".join(diff)
    return text[-3900:] if text else "Изменений нет."


def write_file(rel: str, content: str) -> str:
    p = safe_path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"✅ Записал {p.relative_to(WORKSPACE.resolve())}"


def read_file(rel: str) -> str:
    p = safe_path(rel)
    if not p.exists() or not p.is_file():
        raise ValueError("Файл не найден")
    return p.read_text(encoding="utf-8")[:3900]


def list_files(rel: str = ".") -> str:
    p = safe_path(rel)
    if not p.exists():
        return "Путь не найден."
    if p.is_file():
        return str(p.relative_to(WORKSPACE.resolve()))
    items = []
    for x in p.rglob("*"):
        if x.is_file():
            items.append(str(x.relative_to(WORKSPACE.resolve())))
            if len(items) >= 80:
                break
    return "\n".join(items) or "Файлов нет."
