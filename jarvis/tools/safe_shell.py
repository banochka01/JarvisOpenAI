import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from jarvis.config import WORKSPACE
from jarvis.tools.file_tool import read_file


READ_ONLY = "read_only"
RISKY = "risky"
FORBIDDEN = "forbidden"

SHELL_OPERATORS = ("&", "|", ";", "<", ">", "\n", "\r", "`")
SENSITIVE_PARTS = (
    "c:\\users",
    "\\appdata\\",
    "cookies",
    "cookie",
    "token",
    "tokens",
    "credentials",
    "browser",
    "login data",
)
FORBIDDEN_WORDS = (
    "del",
    "erase",
    "rmdir",
    "rd",
    "format",
    "shutdown",
    "reg",
    "powershell",
    "pwsh",
    "cmd",
    "curl",
    "wget",
    "start",
)


@dataclass(frozen=True)
class CommandCheck:
    allowed: bool
    category: str
    reason: str
    argv: tuple[str, ...] = ()


def _workspace() -> Path:
    return WORKSPACE.resolve()


def _parse(command: str) -> list[str]:
    return shlex.split(command.strip(), posix=False)


def _has_shell_operator(command: str) -> bool:
    return any(op in command for op in SHELL_OPERATORS)


def _looks_like_absolute_path(value: str) -> bool:
    value = value.strip('"\'')
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _path_allowed(value: str) -> bool:
    value = value.strip('"\'')
    path_parts = set(Path(value).parts) | set(PureWindowsPath(value).parts)
    if ".." in path_parts:
        return False
    if not _looks_like_absolute_path(value):
        return True
    try:
        Path(value).resolve().relative_to(_workspace())
        return True
    except ValueError:
        return False


def _contains_sensitive_reference(command: str) -> bool:
    c = command.lower()
    return any(part in c for part in SENSITIVE_PARTS)


def _has_forbidden_word(argv: list[str]) -> bool:
    return bool(argv and argv[0].lower() in FORBIDDEN_WORDS)


def _is_git_read_only(argv: list[str]) -> bool:
    if argv[:2] in (["git", "status"], ["git", "diff"], ["git", "log"]):
        return True
    if argv[:2] == ["git", "branch"]:
        allowed_args = {"--list", "--show-current", "-a", "-r", "-vv", "-v"}
        return len(argv) == 2 or all(arg in allowed_args for arg in argv[2:])
    return False


def _is_builtin_read(argv: list[str]) -> bool:
    if not argv:
        return False
    command = argv[0].lower()
    return command in {"dir", "ls"} or (command in {"cat", "type"} and len(argv) >= 2)


def _is_risky_allowed(argv: list[str]) -> bool:
    if not argv:
        return False
    command = argv[0].lower()
    if argv[:2] in (["git", "add"], ["git", "commit"]):
        return True
    if argv[:2] == ["npm", "install"] or argv[:2] == ["npm", "run"]:
        return True
    if command in {"npx", "node", "python", "py"}:
        return True
    if argv[:2] == ["docker", "compose"]:
        return True
    return False


def classify_command(command: str) -> CommandCheck:
    raw = command.strip()
    if not raw:
        return CommandCheck(False, FORBIDDEN, "empty command")
    if _has_shell_operator(raw):
        return CommandCheck(False, FORBIDDEN, "shell operators are forbidden")
    if _contains_sensitive_reference(raw):
        return CommandCheck(False, FORBIDDEN, "sensitive paths or secrets are forbidden")
    try:
        argv = _parse(raw)
    except ValueError as exc:
        return CommandCheck(False, FORBIDDEN, f"cannot parse command: {exc}")
    if not argv:
        return CommandCheck(False, FORBIDDEN, "empty command")
    lower_argv = [part.lower() for part in argv]
    if _has_forbidden_word(lower_argv):
        return CommandCheck(False, FORBIDDEN, "command executable is forbidden")
    if not all(_path_allowed(arg) for arg in argv[1:]):
        return CommandCheck(False, FORBIDDEN, "absolute paths outside workspace are forbidden")
    if _is_git_read_only(lower_argv) or _is_builtin_read(lower_argv):
        return CommandCheck(True, READ_ONLY, "read-only command", tuple(argv))
    if _is_risky_allowed(lower_argv):
        return CommandCheck(True, RISKY, "risky command requires approval", tuple(argv))
    return CommandCheck(False, FORBIDDEN, "command is not in whitelist")


def is_safe_command(command: str) -> tuple[bool, str]:
    check = classify_command(command)
    return check.allowed, check.reason


def is_read_only_command(command: str) -> bool:
    check = classify_command(command)
    return check.allowed and check.category == READ_ONLY


def command_category(command: str) -> str:
    return classify_command(command).category


def _run_builtin(argv: tuple[str, ...]) -> str | None:
    command = argv[0].lower()
    if command in {"dir", "ls"}:
        rel = argv[1] if len(argv) > 1 else "."
        base = (WORKSPACE / rel).resolve()
        base.relative_to(_workspace())
        if not base.exists():
            return f"Path not found: {rel}"
        if base.is_file():
            return str(base.relative_to(_workspace()))
        items = sorted(p.name + ("/" if p.is_dir() else "") for p in base.iterdir())
        return "\n".join(items) or "Directory is empty."
    if command in {"cat", "type"}:
        return read_file(argv[1])
    return None


def run_safe(command: str, timeout: int = 180) -> str:
    check = classify_command(command)
    if not check.allowed:
        return f"⛔ Запрещено: {check.reason}\nКоманда: {command}"
    try:
        builtin = _run_builtin(check.argv)
        if builtin is not None:
            return builtin[-3900:]
        res = subprocess.run(
            list(check.argv),
            cwd=_workspace(),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (res.stdout or "") + (res.stderr or "")
        if out:
            return out[-3900:]
        return f"Команда завершилась с кодом {res.returncode}"
    except subprocess.TimeoutExpired:
        return "⏱ Команда зависла/превысила timeout."
    except Exception as e:
        return f"Ошибка запуска: {e}"
