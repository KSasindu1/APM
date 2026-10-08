from __future__ import annotations

from datetime import date, timedelta

from app.model.planning import (
    RiskInput,
    RiskLevel,
    TaskInput,
    TaskPriority,
)


PRIORITY_WEIGHT = {
    TaskPriority.CRITICAL: 4,
    TaskPriority.HIGH: 3,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 1,
}


RISK_BUFFER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 4,
}


def sort_tasks_by_priority(
    tasks: list[TaskInput],
) -> list[TaskInput]:
    return sorted(
        tasks,
        key=lambda task: (
            -PRIORITY_WEIGHT[task.priority],
            task.task_id,
        ),
    )


def validate_unique_task_ids(
    tasks: list[TaskInput],
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()

    for task in tasks:
        if task.task_id in seen:
            errors.append(
                f"Duplicate task ID: {task.task_id}"
            )

        seen.add(task.task_id)

    return errors


def validate_dependencies(
    tasks: list[TaskInput],
) -> list[str]:
    errors: list[str] = []

    task_ids = {
        task.task_id
        for task in tasks
    }

    for task in tasks:
        for dependency in task.dependencies:

            if dependency not in task_ids:
                errors.append(
                    f"Task {task.task_id} depends on "
                    f"unknown task {dependency}"
                )

            if dependency == task.task_id:
                errors.append(
                    f"Task {task.task_id} cannot depend "
                    "on itself."
                )

    return errors


def detect_dependency_cycle(
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


def calculate_risk_buffer(
    task_id: str,
    risks: list[RiskInput],
) -> tuple[RiskLevel, int]:

    task_risks = [
        risk
        for risk in risks
        if risk.task_id == task_id
    ]

    if not task_risks:
        return RiskLevel.LOW, 0

    highest_risk = max(
        task_risks,
        key=lambda risk: (
            RISK_BUFFER[risk.risk_level],
            risk.impact_days,
        ),
    )

    buffer_days = max(
        RISK_BUFFER[risk.risk_level]
        + risk.impact_days
        for risk in task_risks
    )

    return (
        highest_risk.risk_level,
        buffer_days,
    )


def topological_order(
    tasks: list[TaskInput],
) -> list[TaskInput]:

    dependency_errors = validate_dependencies(
        tasks
    )

    if dependency_errors:
        raise ValueError(
            "; ".join(dependency_errors)
        )

    if detect_dependency_cycle(tasks):
        raise ValueError(
            "Dependency graph contains a cycle."
        )

    task_map = {
        task.task_id: task
        for task in tasks
    }

    indegree = {
        task.task_id: len(task.dependencies)
        for task in tasks
    }

    children = {
        task.task_id: []
        for task in tasks
    }

    for task in tasks:
        for dependency in task.dependencies:
            children[
                dependency
            ].append(task.task_id)

    ready = [
        task
        for task in tasks
        if indegree[task.task_id] == 0
    ]

    ready = sort_tasks_by_priority(
        ready
    )

    ordered: list[TaskInput] = []

    while ready:
        current = ready.pop(0)

        ordered.append(current)

        for child_id in children[
            current.task_id
        ]:
            indegree[child_id] -= 1

            if indegree[child_id] == 0:
                ready.append(
                    task_map[child_id]
                )

        ready = sort_tasks_by_priority(
            ready
        )

    if len(ordered) != len(tasks):
        raise ValueError(
            "Dependency graph contains a cycle."
        )

    return ordered


def schedule_tasks(
    tasks: list[TaskInput],
    start_date: date,
    risks: list[RiskInput],
) -> list[dict]:

    ordered_tasks = topological_order(
        tasks
    )

    scheduled: dict[str, dict] = {}

    for task in ordered_tasks:

        risk_level, risk_buffer = (
            calculate_risk_buffer(
                task.task_id,
                risks,
            )
        )

        if task.dependencies:

            dependency_end_dates = [
                scheduled[
                    dependency
                ]["end_date"]
                for dependency in task.dependencies
            ]

            earliest_start = (
                max(dependency_end_dates)
                + timedelta(days=1)
            )

        else:
            earliest_start = start_date

        actual_duration = (
            task.duration_days
            + risk_buffer
        )

        end_date = (
            earliest_start
            + timedelta(
                days=actual_duration - 1
            )
        )

        scheduled[
            task.task_id
        ] = {
            "task": task,
            "start_date": earliest_start,
            "end_date": end_date,
            "duration": actual_duration,
            "risk_level": risk_level,
            "risk_buffer": risk_buffer,
        }

    return list(
        scheduled.values()
    )


def calculate_critical_path(
    scheduled_items: list,
) -> tuple[list[str], int]:
    """
    Calculate the longest dependency path.

    Supports both:

    1. Raw TaskInput objects.
    2. Scheduled dictionaries produced by
       schedule_tasks().
    """

    if not scheduled_items:
        return [], 0

    duration_by_id: dict[str, int] = {}

    dependencies_by_id: dict[
        str,
        list[str],
    ] = {}

    # Normalize both supported input formats.
    for item in scheduled_items:

        # --------------------------------------------------
        # Raw TaskInput
        # --------------------------------------------------
        if isinstance(item, TaskInput):

            task = item

            duration_by_id[
                task.task_id
            ] = task.duration_days

            dependencies_by_id[
                task.task_id
            ] = list(task.dependencies)

        # --------------------------------------------------
        # Scheduled dictionary
        # --------------------------------------------------
        elif isinstance(item, dict):

            if "task" not in item:
                raise ValueError(
                    "Scheduled item is missing the 'task' field."
                )

            task = item["task"]

            if not isinstance(
                task,
                TaskInput,
            ):
                raise TypeError(
                    "Scheduled item 'task' must be "
                    "a TaskInput instance."
                )

            duration_by_id[
                task.task_id
            ] = int(
                item.get(
                    "duration",
                    task.duration_days,
                )
            )

            dependencies_by_id[
                task.task_id
            ] = list(task.dependencies)

        else:
            raise TypeError(
                "calculate_critical_path() expects "
                "TaskInput objects or scheduled dictionaries."
            )

    # --------------------------------------------------
    # Validate dependency references
    # --------------------------------------------------

    task_ids = set(
        duration_by_id
    )

    for task_id, dependencies in (
        dependencies_by_id.items()
    ):

        for dependency in dependencies:

            if dependency not in task_ids:
                raise ValueError(
                    f"Task {task_id} depends on "
                    f"unknown task {dependency}"
                )

    # --------------------------------------------------
    # Rebuild normalized TaskInput objects.
    # This allows us to reuse the existing dependency
    # validation and topological ordering logic.
    # --------------------------------------------------

    normalized_tasks = [
        TaskInput(
            task_id=task_id,
            name=task_id,
            duration_days=duration_by_id[
                task_id
            ],
            dependencies=dependencies_by_id[
                task_id
            ],
        )
        for task_id in task_ids
    ]

    # --------------------------------------------------
    # Detect circular dependencies
    # --------------------------------------------------

    if detect_dependency_cycle(
        normalized_tasks
    ):
        raise ValueError(
            "Dependency graph contains a cycle."
        )

    # --------------------------------------------------
    # Get deterministic topological order
    # --------------------------------------------------

    ordered_tasks = topological_order(
        normalized_tasks
    )

    # --------------------------------------------------
    # Calculate longest path
    # --------------------------------------------------

    longest_duration: dict[str, int] = {}

    predecessor: dict[
        str,
        str | None,
    ] = {}

    for task in ordered_tasks:

        task_id = task.task_id

        # Task with no dependencies starts a path.
        if not task.dependencies:

            longest_duration[
                task_id
            ] = duration_by_id[
                task_id
            ]

            predecessor[
                task_id
            ] = None

            continue

        # Find the dependency with the longest
        # path leading into this task.
        best_dependency = max(
            task.dependencies,
            key=lambda dependency:
            longest_duration[dependency],
        )

        longest_duration[
            task_id
        ] = (
            longest_duration[
                best_dependency
            ]
            + duration_by_id[
                task_id
            ]
        )

        predecessor[
            task_id
        ] = best_dependency

    # --------------------------------------------------
    # Find task at the end of the longest path
    # --------------------------------------------------

    final_task = max(
        longest_duration,
        key=longest_duration.get,
    )

    # --------------------------------------------------
    # Reconstruct critical path
    # --------------------------------------------------

    critical_path: list[str] = []

    current: str | None = final_task

    while current is not None:

        critical_path.append(
            current
        )

        current = predecessor.get(
            current
        )

    critical_path.reverse()

    # --------------------------------------------------
    # Return path and total duration
    # --------------------------------------------------

    return (
        critical_path,
        longest_duration[
            final_task
        ],
    )