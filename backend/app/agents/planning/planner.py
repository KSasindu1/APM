import json
from datetime import date
from typing import List, Optional

from app.agents.planning.prompts import TASK_DECOMPOSITION_PROMPT
from app.model.planning import TaskInput, TaskPriority


class TaskPlanner:

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def generate_tasks(
        self,
        project_name: str,
        objective: str,
        start_date: date,
        deadline: date,
    ) -> tuple[List[TaskInput], float]:

        if self.llm_client:

            try:
                tasks = self._generate_with_llm(
                    project_name,
                    objective,
                    start_date,
                    deadline,
                )

                if tasks:
                    return tasks, 0.85

            except Exception:
                # Safe fallback
                pass

        return self._fallback_tasks(objective), 0.60

    def _generate_with_llm(
        self,
        project_name: str,
        objective: str,
        start_date: date,
        deadline: date,
    ) -> List[TaskInput]:

        prompt = TASK_DECOMPOSITION_PROMPT.format(
            project_name=project_name,
            objective=objective,
            start_date=start_date.isoformat(),
            deadline=deadline.isoformat(),
        )

        response = self.llm_client.generate(prompt)

        if not response:
            return []

        if isinstance(response, str):
            response = json.loads(response)

        tasks = []

        for item in response:

            tasks.append(
                TaskInput(
                    task_id=item["task_id"],
                    task_name=item["task_name"],
                    description=item.get("description"),
                    duration_days=item["duration_days"],
                    priority=item.get(
                        "priority",
                        TaskPriority.MEDIUM,
                    ),
                    dependencies=item.get(
                        "dependencies",
                        [],
                    ),
                )
            )

        return tasks

    def _fallback_tasks(
        self,
        objective: str,
    ) -> List[TaskInput]:

        return [
            TaskInput(
                task_id="T001",
                task_name="Requirements Analysis",
                description=(
                    "Analyze project requirements and "
                    "define the project scope."
                ),
                duration_days=3,
                priority=TaskPriority.HIGH,
                dependencies=[],
            ),
            TaskInput(
                task_id="T002",
                task_name="Project Planning",
                description=(
                    "Define project activities, resources "
                    "and implementation approach."
                ),
                duration_days=2,
                priority=TaskPriority.HIGH,
                dependencies=["T001"],
            ),
            TaskInput(
                task_id="T003",
                task_name="Implementation",
                description=(
                    "Implement the main project functionality."
                ),
                duration_days=7,
                priority=TaskPriority.CRITICAL,
                dependencies=["T002"],
            ),
            TaskInput(
                task_id="T004",
                task_name="Testing",
                description=(
                    "Test the implemented functionality."
                ),
                duration_days=3,
                priority=TaskPriority.HIGH,
                dependencies=["T003"],
            ),
            TaskInput(
                task_id="T005",
                task_name="Deployment",
                description=(
                    "Deploy and configure the completed project."
                ),
                duration_days=2,
                priority=TaskPriority.MEDIUM,
                dependencies=["T004"],
            ),
        ]