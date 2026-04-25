from jarvis.text_utils import chunks, parse_task_ref


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
