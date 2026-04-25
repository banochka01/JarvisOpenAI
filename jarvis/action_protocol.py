import json
from dataclasses import dataclass, field
from typing import Any

from jarvis.tools.file_tool import preview_diff
from jarvis.tools.safe_shell import classify_command


ALLOWED_ACTION_TYPES = {"shell", "write_file", "steam"}
VALID_ACTION_TYPES = ALLOWED_ACTION_TYPES | {"rejected"}


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    payload: dict[str, Any]
    title: str
    risk: str
    preview: str = ""


@dataclass(frozen=True)
class SupervisorPlan:
    needs_clarification: bool = False
    questions: list[str] = field(default_factory=list)
    summary: str = ""
    plan: list[str] = field(default_factory=list)
    proposed_actions: list[ProposedAction] = field(default_factory=list)
    raw_text: str = ""


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON object not found")
    return json.loads(stripped[start:end + 1])


def action_from_dict(raw: dict[str, Any]) -> ProposedAction:
    action_type = str(raw.get("type", "")).strip()
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError(f"unsupported action type: {action_type}")

    if action_type == "shell":
        command = str(raw.get("command", "")).strip()
        check = classify_command(command)
        if not check.allowed:
            raise ValueError(f"shell action rejected: {check.reason}")
        return ProposedAction(
            action_type="shell",
            payload={"command": command},
            title=f"Shell: {command}",
            risk=check.category,
            preview=check.reason,
        )

    if action_type == "write_file":
        path = str(raw.get("path", "")).strip()
        content = str(raw.get("content", ""))
        if not path:
            raise ValueError("write_file path is required")
        return ProposedAction(
            action_type="write_file",
            payload={"path": path, "content": content},
            title=f"Write file: {path}",
            risk="risky",
            preview=preview_diff(path, content),
        )

    app_id = "".join(ch for ch in str(raw.get("app_id", "")) if ch.isdigit())
    if not app_id:
        raise ValueError("steam app_id must be numeric")
    return ProposedAction(
        action_type="steam",
        payload={"app_id": app_id},
        title=f"Steam install: {app_id}",
        risk="risky",
        preview=f"steam://install/{app_id}",
    )


def parse_supervisor_plan(text: str) -> SupervisorPlan:
    try:
        data = _extract_json(text)
    except Exception:
        return SupervisorPlan(raw_text=text, summary=text.strip())

    actions = []
    for item in data.get("proposed_actions", []) or []:
        try:
            actions.append(action_from_dict(item))
        except Exception as exc:
            actions.append(ProposedAction(
                action_type="rejected",
                payload={"raw": item, "reason": str(exc)},
                title="Rejected action",
                risk="forbidden",
                preview=str(exc),
            ))

    questions = data.get("questions", []) or []
    plan = data.get("plan", []) or []
    return SupervisorPlan(
        needs_clarification=bool(data.get("needs_clarification", False)),
        questions=[str(q) for q in questions],
        summary=str(data.get("summary", "")).strip(),
        plan=[str(step) for step in plan],
        proposed_actions=actions,
        raw_text=text,
    )


def format_plan_for_human(plan: SupervisorPlan) -> str:
    parts = []
    if plan.summary:
        parts.append(plan.summary)
    if plan.needs_clarification and plan.questions:
        parts.append("Уточнения:\n" + "\n".join(f"- {q}" for q in plan.questions))
    if plan.plan:
        parts.append("План:\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(plan.plan, 1)))
    if plan.proposed_actions:
        lines = []
        for action in plan.proposed_actions:
            lines.append(f"- {action.title} [{action.risk}]")
        parts.append("Proposed actions:\n" + "\n".join(lines))
    return "\n\n".join(parts) or plan.raw_text
