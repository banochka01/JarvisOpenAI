import os


def install_steam_game(app_id: str) -> str:
    app_id = "".join(ch for ch in app_id if ch.isdigit())
    if not app_id:
        return "Нужен числовой Steam app_id."
    try:
        os.startfile(f"steam://install/{app_id}")  # type: ignore[attr-defined]
    except Exception as exc:
        return f"Не удалось открыть Steam install для app_id={app_id}: {exc}"
    return f"Открыл Steam установку app_id={app_id}. Если Steam спросит подтверждение, нажми сам."
