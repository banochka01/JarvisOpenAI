import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int_env(name: str, default: int = 0) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} должен быть числом") from exc


def _inside_root(path: Path, name: str) -> Path:
    root = ROOT.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{name} должен быть внутри проекта: {root}") from exc
    return resolved


def _project_path_env(name: str, default: Path) -> Path:
    raw = _env(name)
    path = Path(raw) if raw else default
    if not path.is_absolute():
        path = ROOT / path
    return _inside_root(path, name)


def _missing_secret(value: str) -> bool:
    return not value or value.startswith("PASTE_") or value in {"твой OpenAI API key", "токен от @BotFather"}


TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = _int_env("ALLOWED_USER_ID", 0)
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4.1-mini")
WORKSPACE = _project_path_env("WORKSPACE", ROOT / "jarvis" / "workspace")
DB_PATH = _project_path_env("DB_PATH", ROOT / "jarvis" / "storage" / "jarvis.db")
AUTO_APPROVE_SAFE_COMMANDS = _env("AUTO_APPROVE_SAFE_COMMANDS", "0") == "1"
STT_PROVIDER = _env("STT_PROVIDER", "openai").lower()
STT_SERVER_URL = _env("STT_SERVER_URL")
STT_SERVER_TOKEN = _env("STT_SERVER_TOKEN")
STT_SERVER_TIMEOUT = _int_env("STT_SERVER_TIMEOUT", 120)

WORKSPACE.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def validate_bot_config() -> None:
    if _missing_secret(TELEGRAM_BOT_TOKEN):
        raise RuntimeError("TELEGRAM_BOT_TOKEN не указан в .env")
    if ALLOWED_USER_ID <= 0:
        raise RuntimeError("ALLOWED_USER_ID должен быть указан в .env")


def validate_openai_config() -> None:
    if _missing_secret(OPENAI_API_KEY):
        raise RuntimeError("OPENAI_API_KEY не указан в .env")
