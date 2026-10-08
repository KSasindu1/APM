from __future__ import annotations

import json
import logging
import os
import re
from datetime import date

from groq import Groq

from app.agents.planning.prompts import (
    TASK_DECOMPOSITION_PROMPT,
)
from app.model.planning import TaskInput

logger = logging.getLogger(__name__)


class PlanningAgent:
    """
    Generates project tasks using Groq when available.

    If Groq is unavailable or the LLM response is invalid,
    a deterministic fallback plan is returned.
    """

    def __init__(self) -> None:
        self.api_key = (
            os.getenv("GROQ_API_KEY")
            or os.getenv("LLM_API_KEY")
        )

        self.model = os.getenv(
            "GROQ_MODEL",
            "openai/gpt-oss-120b",
        ).strip()

        self.client: Groq | None = None

        self.last_generation_was_llm = False

        if self.api_key:
            self.client = Groq(
                api_key=self.api_key,
            )

    @staticmethod
    def sanitize_text(text: str) -> str:
        text = text.strip()

        return re.sub(
            r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]",
            "",
            text,
        )

    def generate_tasks(
        self,
        project_name: str,
        objective: str,
        start_date: date,
        deadline: date,
    ) -> list[TaskInput]:

        self.last_generation_was_llm = False

        project_name = self.sanitize_text(
            project_name
        )

        objective = self.sanitize_text(
            objective
        )

        if deadline < start_date:
            raise ValueError(
                "Project deadline cannot be before project start date."
            )

        if not self.client:
            logger.warning(
                "GROQ_API_KEY is not configured. "
                "Using deterministic fallback tasks."
            )

            return self.fallback_tasks(
                objective=objective,
            )

        available_days = (
            deadline - start_date
        ).days + 1

        try:
            prompt = TASK_DECOMPOSITION_PROMPT.format(
                project_name=project_name,
                objective=objective,
                start_date=start_date.isoformat(),
                deadline=deadline.isoformat(),
                available_days=available_days,
            )
        except Exception as exc:
            logger.exception(
                "Failed to format planning prompt: %s",
                exc,
            )

            return self.fallback_tasks(
                objective=objective,
            )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional project "
                            "planning agent. Return only valid "
                            "JSON. Do not include Markdown, "
                            "comments, explanations, or extra text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.1,
                max_completion_tokens=4000,
                response_format={
                    "type": "json_object",
                },
            )

            if not response.choices:
                raise ValueError(
                    "Groq returned no choices."
                )

            content = response.choices[0].message.content

            if not content:
                raise ValueError(
                    "Groq returned an empty response."
                )

            parsed = self.parse_llm_json(content)

            tasks = self.convert_to_tasks(parsed)

            if not tasks:
                raise ValueError(
                    "LLM generated no tasks."
                )

            self.validate_generated_tasks(tasks)

            self.last_generation_was_llm = True

            logger.info(
                "LLM generated %d valid project tasks.",
                len(tasks),
            )

            return tasks

        except Exception as exc:
            logger.exception(
                "Groq task generation failed: %s",
                exc,
            )

            self.last_generation_was_llm = False

            logger.warning(
                "Using deterministic fallback task plan."
            )

            return self.fallback_tasks(
                objective=objective,
            )

    @classmethod
    def parse_llm_json(
        cls,
        content: str,
    ) -> list[dict]:

        cleaned = cls.clean_json_response(content)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON returned by LLM: {exc}"
            ) from exc

        if isinstance(parsed, list):
            return parsed

        if isinstance(parsed, dict):
            tasks = parsed.get("tasks")

            if isinstance(tasks, list):
                return tasks

        raise ValueError(
            "LLM response must contain a tasks array."
        )

    @staticmethod
    def convert_to_tasks(
        parsed: list[dict],
    ) -> list[TaskInput]:

        if not isinstance(parsed, list):
            raise ValueError(
                "Task data must be a JSON array."
            )

        tasks: list[TaskInput] = []

        for index, item in enumerate(
            parsed,
            start=1,
        ):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Task {index} must be a JSON object."
                )

            item = dict(item)

            # Backward compatibility.
            if (
                "task_name" in item
                and "name" not in item
            ):
                item["name"] = item.pop(
                    "task_name"
                )

            if not item.get("task_id"):
                item["task_id"] = (
                    f"T{index:03d}"
                )

            if (
                "dependencies" not in item
                or item["dependencies"] is None
            ):
                item["dependencies"] = []

            if not isinstance(
                item["dependencies"],
                list,
            ):
                item["dependencies"] = [
                    str(item["dependencies"])
                ]

            if "priority" in item:
                item["priority"] = str(
                    item["priority"]
                ).strip().upper()

            if "duration_days" in item:
                try:
                    item["duration_days"] = int(
                        item["duration_days"]
                    )
                except (
                    TypeError,
                    ValueError,
                ) as exc:
                    raise ValueError(
                        f"Task {index} has an invalid "
                        "duration_days value."
                    ) from exc

            try:
                tasks.append(
                    TaskInput(**item)
                )
            except Exception as exc:
                raise ValueError(
                    f"Invalid task {index}: {exc}"
                ) from exc

        return tasks

    @staticmethod
    def validate_generated_tasks(
        tasks: list[TaskInput],
    ) -> None:

        task_ids = {
            task.task_id
            for task in tasks
        }

        if len(task_ids) != len(tasks):
            raise ValueError(
                "LLM generated duplicate task IDs."
            )

        for task in tasks:
            for dependency in task.dependencies:

                if dependency not in task_ids:
                    raise ValueError(
                        f"Task {task.task_id} has unknown "
                        f"dependency: {dependency}"
                    )

                if dependency == task.task_id:
                    raise ValueError(
                        f"Task {task.task_id} cannot depend "
                        "on itself."
                    )

        if PlanningAgent.has_dependency_cycle(
            tasks
        ):
            raise ValueError(
                "LLM generated a circular dependency."
            )

    @staticmethod
    def has_dependency_cycle(
        tasks: list[TaskInput],
    ) -> bool:

        graph = {
            task.task_id: task.dependencies
            for task in tasks
        }

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True

            if node in visited:
                return False

            visiting.add(node)

            for dependency in graph.get(
                node,
                [],
            ):
                if dependency in graph:
                    if visit(dependency):
                        return True

            visiting.remove(node)
            visited.add(node)

            return False

        return any(
            visit(task_id)
            for task_id in graph
        )

    @staticmethod
    def clean_json_response(
        content: str,
    ) -> str:

        content = content.strip()

        content = re.sub(
            r"^```(?:json)?\s*",
            "",
            content,
            flags=re.IGNORECASE,
        )

        content = re.sub(
            r"\s*```$",
            "",
            content,
        )

        content = content.strip()

        if content.startswith("{"):
            end = content.rfind("}")

            if end == -1:
                raise ValueError(
                    "Invalid JSON object returned by LLM."
                )

            return content[: end + 1]

        start = content.find("[")
        end = content.rfind("]")

        if (
            start == -1
            or end == -1
            or end < start
        ):
            raise ValueError(
                "No valid JSON task array found."
            )

        return content[start:end + 1]

    @staticmethod
    def fallback_tasks(
        objective: str = "",
    ) -> list[TaskInput]:

        objective_summary = (
            objective.strip()
            if objective
            else "the project"
        )

        return [
            TaskInput(
                task_id="T001",
                name="Requirements Analysis",
                description=(
                    "Analyze requirements and define "
                    f"the scope of {objective_summary}."
                ),
                duration_days=3,
                priority="HIGH",
                dependencies=[],
            ),
            TaskInput(
                task_id="T002",
                name="Project Planning",
                description=(
                    "Define project activities, resources "
                    "and implementation approach."
                ),
                duration_days=2,
                priority="HIGH",
                dependencies=["T001"],
            ),
            TaskInput(
                task_id="T003",
                name="System Design",
                description=(
                    "Design the solution architecture "
                    "and major components."
                ),
                duration_days=3,
                priority="HIGH",
                dependencies=["T002"],
            ),
            TaskInput(
                task_id="T004",
                name="Implementation",
                description=(
                    "Implement the primary project "
                    "functionality."
                ),
                duration_days=7,
                priority="CRITICAL",
                dependencies=["T003"],
            ),
            TaskInput(
                task_id="T005",
                name="Testing",
                description=(
                    "Perform functional and integration "
                    "testing."
                ),
                duration_days=3,
                priority="HIGH",
                dependencies=["T004"],
            ),
            TaskInput(
                task_id="T006",
                name="Deployment",
                description=(
                    "Deploy and configure the completed "
                    "solution."
                ),
                duration_days=2,
                priority="MEDIUM",
                dependencies=["T005"],
            ),
        ]