from datetime import date, timedelta
from typing import Dict, List, Set, Tuple

from app.model.planning import (
    RiskInput,
    ScheduledTask,
    TaskInput,
)


PRIORITY_WEIGHT = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


class SchedulingError(Exception):
    pass


class Scheduler:

    def validate_tasks(self, tasks: List[TaskInput]) -> None:
        task_ids = {task.task_id for task in tasks}

        if len(task_ids) != len(tasks):
            raise SchedulingError(
                "Duplicate task IDs were detected."
            )

        for task in tasks:
            for dependency in task.dependencies:
                if dependency not in task_ids:
                    raise SchedulingError(
                        f"Task '{task.task_id}' depends on "
                        f"unknown task '{dependency}'."
                    )

        if self.detect_cycle(tasks):
            raise SchedulingError(
                "Circular dependency detected in project tasks."
            )

    def detect_cycle(self, tasks: List[TaskInput]) -> bool:
        graph = {
            task.task_id: task.dependencies
            for task in tasks
        }

        visiting: Set[str] = set()
        visited: Set[str] = set()

        def dfs(task_id: str) -> bool:
            if task_id in visiting:
                return True

            if task_id in visited:
                return False

            visiting.add(task_id)

            for dependency in graph.get(task_id, []):
                if dfs(dependency):
                    return True

            visiting.remove(task_id)
            visited.add(task_id)

            return False

        for task_id in graph:
            if dfs(task_id):
                return True

        return False

    def apply_risks(
        self,
        tasks: List[TaskInput],
        risks: List[RiskInput],
    ) -> Dict[str, int]:

        additional_days: Dict[str, int] = {
            task.task_id: 0
            for task in tasks
        }

        for risk in risks:

            if not risk.task_id:
                continue

            if risk.task_id not in additional_days:
                continue

            additional_days[risk.task_id] += risk.delay_days

        return additional_days

    def sort_ready_tasks(
        self,
        tasks: List[TaskInput],
    ) -> List[TaskInput]:

        return sorted(
            tasks,
            key=lambda task: (
                -PRIORITY_WEIGHT[task.priority.value],
                task.task_id,
            ),
        )

    def calculate_schedule(
        self,
        tasks: List[TaskInput],
        start_date: date,
        risks: List[RiskInput],
    ) -> Tuple[List[ScheduledTask], List[str]]:

        self.validate_tasks(tasks)

        risk_delays = self.apply_risks(tasks, risks)

        task_map = {
            task.task_id: task
            for task in tasks
        }

        completed: Set[str] = set()

        schedule: Dict[str, ScheduledTask] = {}

        warnings: List[str] = []

        current_date = start_date

        while len(completed) < len(tasks):

            ready_tasks = [
                task
                for task in tasks
                if task.task_id not in completed
                and all(
                    dependency in completed
                    for dependency in task.dependencies
                )
            ]

            if not ready_tasks:
                raise SchedulingError(
                    "Unable to continue scheduling. "
                    "Dependency graph may contain a cycle."
                )

            ready_tasks = self.sort_ready_tasks(ready_tasks)

            for task in ready_tasks:

                dependency_end_dates = [
                    schedule[dependency].end_date
                    for dependency in task.dependencies
                ]

                if dependency_end_dates:
                    task_start = max(
                        current_date,
                        max(dependency_end_dates) + timedelta(days=1),
                    )
                else:
                    task_start = current_date

                extra_days = risk_delays.get(task.task_id, 0)

                total_duration = (
                    task.duration_days + extra_days
                )

                task_end = (
                    task_start
                    + timedelta(days=total_duration - 1)
                )

                rationale_parts = [
                    f"Priority: {task.priority.value}",
                    f"Base duration: {task.duration_days} day(s)",
                ]

                if extra_days:
                    rationale_parts.append(
                        f"Risk buffer: {extra_days} day(s)"
                    )

                    warnings.append(
                        f"Risk buffer of {extra_days} day(s) "
                        f"added to task '{task.task_id}'."
                    )

                if task.dependencies:
                    rationale_parts.append(
                        "Dependencies completed before task start."
                    )

                scheduled_task = ScheduledTask(
                    task_id=task.task_id,
                    task_name=task.task_name,
                    start_date=task_start,
                    end_date=task_end,
                    duration_days=total_duration,
                    priority=task.priority,
                    dependencies=task.dependencies,
                    assigned_to=task.assigned_to,
                    status=task.status,
                    is_critical=False,
                    rationale=" ".join(rationale_parts),
                )

                schedule[task.task_id] = scheduled_task

                completed.add(task.task_id)

                current_date = max(
                    current_date,
                    task_end + timedelta(days=1),
                )

        ordered_schedule = sorted(
            schedule.values(),
            key=lambda item: item.start_date,
        )

        return ordered_schedule, warnings

    def calculate_critical_path(
        self,
        schedule: List[ScheduledTask],
    ) -> List[str]:

        if not schedule:
            return []

        task_map = {
            task.task_id: task
            for task in schedule
        }

        longest_duration: Dict[str, int] = {}

        predecessor: Dict[str, str] = {}

        ordered = sorted(
            schedule,
            key=lambda task: task.end_date,
        )

        for task in ordered:

            dependency_length = 0
            best_dependency = None

            for dependency in task.dependencies:

                dependency_value = longest_duration.get(
                    dependency,
                    0,
                )

                if dependency_value > dependency_length:
                    dependency_length = dependency_value
                    best_dependency = dependency

            longest_duration[task.task_id] = (
                dependency_length + task.duration_days
            )

            if best_dependency:
                predecessor[task.task_id] = best_dependency

        if not longest_duration:
            return []

        final_task = max(
            longest_duration,
            key=longest_duration.get,
        )

        critical_path = []

        current = final_task

        while current:
            critical_path.append(current)
            current = predecessor.get(current)

        critical_path.reverse()

        for task in schedule:
            if task.task_id in critical_path:
                task.is_critical = True

        return critical_path