"""Task and project calculations; no AI or external service calls."""

from collections import deque
from datetime import date

from .schemas import (
    ActualProjectSnapshot, MonitoringMetrics, MonitoringSettings, PlanningSnapshot,
    RiskSnapshot, TaskAssessment,
)


def expected_progress(task, as_of: date) -> float:
    """Use the planner's target, or a linear calendar-day schedule when absent."""
    if task.expected_progress is not None:
        return task.expected_progress
    if as_of < task.start_date:
        return 0.0
    if as_of >= task.due_date:
        return 100.0
    return round(100 * (as_of - task.start_date).days / (task.due_date - task.start_date).days, 2)


def assess_tasks(planning: PlanningSnapshot, actual: ActualProjectSnapshot,
                 as_of: date, settings: MonitoringSettings) -> list[TaskAssessment]:
    actual_by_id = {task.task_id: task for task in actual.tasks}
    planned_by_id = {task.task_id: task for task in planning.tasks}
    assessments: dict[str, TaskAssessment] = {}
    children = {task_id: [] for task_id in planned_by_id}
    pending = {task.task_id: len(task.dependency_ids) for task in planning.tasks}
    for task in planning.tasks:
        for parent in task.dependency_ids:
            children[parent].append(task.task_id)
    ready = deque(task_id for task_id, count in pending.items() if count == 0)
    while ready:
        task_id = ready.popleft()
        planned = planned_by_id[task_id]
        record = actual_by_id.get(task_id)
        target = expected_progress(planned, as_of)
        progress = record.progress if record else None
        variance = round(progress - target, 2) if progress is not None else None
        completed = record is not None and record.status == "completed"
        overdue = max(0, (as_of - planned.due_date).days) if not completed else 0
        completed_late = (max(0, (record.completed_on - planned.due_date).days)
                          if record is not None and record.completed_on is not None else 0)
        dependency_delayed = any(
            assessments[parent].delayed or assessments[parent].overdue_days > 0
            for parent in planned.dependency_ids
        )
        days_left = (planned.due_date - as_of).days
        approaching = 0 <= days_left <= settings.deadline_approaching_days
        delayed = bool(record and not completed and (
            overdue > 0
            or (variance is not None and variance <= -settings.delay_threshold)
            or (approaching and variance is not None and variance < 0)
            or dependency_delayed
        ))
        assessments[task_id] = TaskAssessment(
            task_id=task_id, title=planned.title,
            status=record.status if record else "missing", actual_progress=progress,
            expected_progress=target, variance=variance,
            overdue_days=overdue, completed_late_days=completed_late,
            delayed=delayed, dependency_delayed=dependency_delayed,
        )
        for child in children[task_id]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    # PlanningSnapshot validation guarantees all references exist and the graph is acyclic.
    return [assessments[task.task_id] for task in planning.tasks]


def calculate_metrics(tasks: list[TaskAssessment], risk: RiskSnapshot | None) -> MonitoringMetrics:
    """An average is only available when every planned task has an actual record."""
    total = len(tasks)
    missing = sum(t.status == "missing" for t in tasks)
    actual = round(sum(t.actual_progress for t in tasks if t.actual_progress is not None) / total, 2) if total and not missing else None
    expected = round(sum(t.expected_progress for t in tasks) / total, 2) if total else None
    return MonitoringMetrics(
        total_tasks=total,
        completed_tasks=sum(t.status == "completed" for t in tasks),
        missing_actual_tasks=missing,
        overdue_tasks=sum(t.overdue_days > 0 for t in tasks),
        blocked_tasks=sum(t.status == "blocked" for t in tasks),
        in_progress_tasks=sum(t.status in ("in_progress", "blocked") for t in tasks),
        not_started_tasks=sum(t.status == "not_started" for t in tasks),
        delayed_tasks=sum(t.delayed for t in tasks),
        high_risks=sum(r.level in ("high", "critical") and r.status == "open" for r in risk.risks) if risk else 0,
        actual_progress=actual,
        expected_progress=expected,
        progress_variance=round(actual - expected, 2) if actual is not None and expected is not None else None,
        actual_progress_is_lower_bound=False,
    )
