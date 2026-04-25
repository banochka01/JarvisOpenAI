from dataclasses import dataclass

from jarvis.action_protocol import ProposedAction, format_plan_for_human, parse_supervisor_plan
from jarvis.agents.llm import build_action_plan
from jarvis.storage.db import JarvisDB


SYSTEM_USER_ID = 0


@dataclass(frozen=True)
class OrchestrationResult:
    task_id: int
    text: str
    approvals: list[int]
    rejected_actions: list[ProposedAction]


class Orchestrator:
    def __init__(self, db: JarvisDB):
        self.db = db

    def plan_task(self, user_text: str, user_id: int = SYSTEM_USER_ID) -> OrchestrationResult:
        task_id = self.db.add_task(user_text[:120] or "Task", user_text, "supervisor", status="planned")
        self.db.add_message("user", user_text, task_id)

        raw = build_action_plan(user_text, self.db.memories())
        plan = parse_supervisor_plan(raw)
        human = format_plan_for_human(plan)
        self.db.add_message("assistant", human, task_id)
        self.db.add_agent_result("supervisor", raw, task_id, "ok")
        self.db.log("orchestrator:plan", f"task_id={task_id}\n{raw}")

        approvals: list[int] = []
        rejected: list[ProposedAction] = []
        for action in plan.proposed_actions:
            if action.action_type == "rejected":
                rejected.append(action)
                self.db.add_agent_result("security", action.preview, task_id, "blockers")
                self.db.set_task_status(task_id, "needs_fix")
                continue
            approval_id = self.db.create_approval(
                user_id,
                action.action_type,
                {"task_id": task_id, **action.payload},
            )
            approvals.append(approval_id)

        return OrchestrationResult(task_id=task_id, text=human, approvals=approvals, rejected_actions=rejected)

    def record_test_result(self, task_id: int, content: str) -> None:
        status = "blockers" if content.strip().upper().startswith("BLOCKERS:") else "ok"
        self.db.add_agent_result("tester", content, task_id, status)
        self.db.set_task_status(task_id, "needs_fix" if status == "blockers" else "testing")

    def record_review_result(self, task_id: int, content: str) -> None:
        status = "blockers" if content.strip().upper().startswith("BLOCKERS:") else "ok"
        self.db.add_agent_result("reviewer", content, task_id, status)
        self.db.set_task_status(task_id, "needs_fix" if status == "blockers" else "done")
