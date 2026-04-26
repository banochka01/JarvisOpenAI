import os


def _clean_app_id(app_id: str) -> str:
    return "".join(ch for ch in str(app_id) if ch.isdigit())


def install_steam_game(app_id: str) -> str:
    app_id = _clean_app_id(app_id)
    if not app_id:
        return "Нужен числовой Steam app_id."
    try:
        os.startfile(f"steam://install/{app_id}")  # type: ignore[attr-defined]
    except Exception as exc:
        return f"Не удалось открыть Steam install для app_id={app_id}: {exc}"
    return f"Открыл Steam установку app_id={app_id}. Если Steam спросит подтверждение, нажми сам."


def launch_steam_game(app_id: str, name: str = "") -> str:
    app_id = _clean_app_id(app_id)
    if not app_id:
        return "Need numeric Steam app_id."
    label = f"{name} ({app_id})" if name else f"app_id={app_id}"
    try:
        os.startfile(f"steam://rungameid/{app_id}")  # type: ignore[attr-defined]
    except Exception as exc:
        return f"Could not launch Steam game {label}: {exc}"
    return f"Launching Steam game: {label}"
