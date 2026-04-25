def approval_title(action_type: str, payload: dict) -> str:
    if action_type == "shell":
        return f"shell: {payload.get('command', '?')}"
    if action_type == "write_file":
        return f"write_file: {payload.get('path', '?')}"
    if action_type == "steam":
        return f"steam: {payload.get('app_id', '?')}"
    if action_type == "mark_done":
        return f"mark_done: task #{payload.get('task_id', '?')}"
    return action_type


def parse_approval_id(args: list[str]) -> int | None:
    if not args:
        return None
    raw = args[0].strip().lstrip("#")
    return int(raw) if raw.isdigit() else None
