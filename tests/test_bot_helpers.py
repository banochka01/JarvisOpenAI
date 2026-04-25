from jarvis.text_utils import (
    assistant_needs_clarification,
    build_clarified_task,
    chunks,
    extract_clarification_questions,
    parse_task_ref,
    strip_new_task_prefix,
)


def test_chunks_splits_long_text():
    parts = chunks("abcde", limit=2)

    assert parts == ["ab", "cd", "e"]


def test_parse_task_ref_with_hash():
    task_id, text = parse_task_ref("#12 review this")

    assert task_id == 12
    assert text == "review this"


def test_parse_task_ref_without_hash():
    task_id, text = parse_task_ref("plain task")

    assert task_id is None
    assert text == "plain task"


def test_parse_task_ref_requires_hash():
    task_id, text = parse_task_ref("12 review this")

    assert task_id is None
    assert text == "12 review this"


def test_clarification_helpers_detect_questions_and_new_task_prefix():
    answer = """1. Нужно ли уточнение? Да.
- Как называется проект?
- Какой телефон показать?"""

    assert assistant_needs_clarification(answer)
    assert extract_clarification_questions(answer) == [
        "Как называется проект?",
        "Какой телефон показать?",
    ]

    is_new, text = strip_new_task_prefix("новая задача: сделай API")

    assert is_new
    assert text == "сделай API"


def test_build_clarified_task_keeps_original_and_answer_together():
    combined = build_clarified_task("сделай заглушку", "название ЦТТ новация")

    assert "Исходная задача:\nсделай заглушку" in combined
    assert "Уточнение пользователя:\nназвание ЦТТ новация" in combined
    assert "Не считай уточнение новой отдельной задачей." in combined
