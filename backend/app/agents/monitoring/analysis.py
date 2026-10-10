"""Deterministic task, milestone, and project analysis; no external service calls."""

from collections import deque
from datetime import date, timedelta
from typing import Literal

from .schemas import (
    ActualProjectSnapshot,
    MilestoneAssessment,
    MonitoringCoverage,
    MonitoringMetrics,
    MonitoringSettings,
    MonitoringWarning,
    PlannedTask,
    PlanningSnapshot,
    RiskSnapshot,
    TaskAssessment,
)


def expected_progress(task: PlannedTask, as_of: date) -> float:
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
        completed = record is not None and record.status == "completed"
        stale = (record is not None and not completed
                 and (as_of - record.updated_on).days > settings.stale_after_days)
        progress = record.progress if record is not None and not stale else None
        variance = round(progress - target, 2) if progress is not None else None
        # Missing or stale unfinished records cannot establish current non-completion.
        overdue = max(0, (as_of - planned.due_date).days) if record is not None and not completed and not stale else 0
        completed_late = (max(0, (record.completed_on - planned.due_date).days)
                          if record is not None and record.completed_on is not None else 0)
        start_delay = (max(0, (record.actual_start - planned.start_date).days)
                       if record is not None and record.actual_start is not None else 0)
        dependency_delayed = any(
            assessments[parent].delayed or assessments[parent].overdue_days > 0
            for parent in planned.dependency_ids
        )
        days_left = (planned.due_date - as_of).days
        approaching = 0 <= days_left <= settings.deadline_approaching_days
        delayed = bool(record is not None and not completed and not stale and (
            overdue > 0
            or start_delay > 0
            or (variance is not None and variance <= -settings.delay_threshold)
            or (approaching and variance is not None and variance < 0)
            or dependency_delayed
        ))
        assessments[task_id] = TaskAssessment(
            task_id=task_id, title=planned.title,
            status="stale" if stale else record.status if record is not None else "missing",
            actual_progress=progress,
            expected_progress=target, variance=variance,
            overdue_days=overdue, completed_late_days=completed_late,
            delayed=delayed, dependency_delayed=dependency_delayed,
            start_delay_days=start_delay,
            planned_deadline_passed=record is None and as_of > planned.due_date,
            actual_is_stale=stale,
            last_observed_progress=record.progress if record is not None and stale else None,
            is_critical=planned.is_critical,
            planned_start=planned.start_date, due_date=planned.due_date,
            actual_start=record.actual_start if record is not None else None,
            completed_on=record.completed_on if record is not None else None,
            last_updated_on=record.updated_on if record is not None else None,
        )
        for child in children[task_id]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
    # PlanningSnapshot validation guarantees all references exist and the graph is acyclic.
    return [assessments[task.task_id] for task in planning.tasks]


def calculate_metrics(tasks: list[TaskAssessment], risk: RiskSnapshot | None) -> MonitoringMetrics:
    """An average requires fresh progress or certain completion for every task."""
    total = len(tasks)
    missing = sum(t.status == "missing" for t in tasks)
    stale = sum(t.status == "stale" or t.actual_is_stale for t in tasks)
    actual = (round(sum(t.actual_progress for t in tasks if t.actual_progress is not None) / total, 2)
              if total and not stale and all(t.actual_progress is not None for t in tasks) else None)
    expected = round(sum(t.expected_progress for t in tasks) / total, 2) if total else None
    return MonitoringMetrics(
        total_tasks=total,
        completed_tasks=sum(t.status == "completed" for t in tasks),
        missing_actual_tasks=missing,
        stale_actual_tasks=stale,
        overdue_tasks=sum(t.status not in ("missing", "stale") and not t.actual_is_stale and t.overdue_days > 0 for t in tasks),
        blocked_tasks=sum(t.status == "blocked" for t in tasks),
        in_progress_tasks=sum(t.status in ("in_progress", "blocked") for t in tasks),
        not_started_tasks=sum(t.status == "not_started" for t in tasks),
        delayed_tasks=sum(t.status not in ("missing", "stale") and not t.actual_is_stale and t.delayed for t in tasks),
        high_risks=sum(r.level in ("high", "critical") and r.status == "open" for r in risk.risks) if risk else 0,
        actual_progress=actual,
        expected_progress=expected,
        progress_variance=round(actual - expected, 2) if actual is not None and expected is not None else None,
        actual_progress_is_lower_bound=False,
    )


def calculate_coverage(planning: PlanningSnapshot, actual: ActualProjectSnapshot,
                       tasks: list[TaskAssessment], risk: RiskSnapshot | None,
                       as_of: date, settings: MonitoringSettings, *,
                       risk_unavailable: bool = False) -> MonitoringCoverage:
    """Describe usable observations, not inferred execution or missing-source success."""
    total = len(tasks)
    missing = sum(task.status == "missing" for task in tasks)
    stale = sum(task.actual_is_stale or task.status == "stale" for task in tasks)
    fresh = total - missing - stale
    if not total or missing == total:
        actual_status = "missing"
    elif stale and not fresh:
        actual_status = "stale"
    elif missing or stale:
        actual_status = "partial"
    else:
        actual_status = "complete"
    planning_freshness = ("unknown" if planning.updated_on is None else
                          "stale" if (as_of - planning.updated_on).days > settings.stale_after_days else "known")
    risk_status = ("unavailable" if risk_unavailable else "not_supplied" if risk is None else
                   "stale" if (as_of - risk.updated_on).days > settings.stale_after_days else "available")
    targets = sum(task.expected_progress is not None for task in planning.tasks)
    method = ("unavailable" if not total else "supplied_target" if targets == total else
              "mixed" if targets else "calendar_estimate")
    return MonitoringCoverage(
        planning_freshness=planning_freshness, actual_status=actual_status,
        risk_status=risk_status, milestones_status="available" if planning.milestones else "not_supplied",
        total_tasks=total, fresh_actual_tasks=fresh, missing_actual_tasks=missing, stale_actual_tasks=stale,
        planning_updated_on=planning.updated_on, planning_observed_on=planning.fetched_on,
        actual_updated_on=(None if actual.context.get("snapshot_date_basis") == "observation" else actual.updated_on),
        actual_observed_on=actual.observed_on,
        risk_observed_on=risk.observed_on if risk is not None else None,
        expected_progress_method=method,
    )


def assess_milestones(planning: PlanningSnapshot, actual: ActualProjectSnapshot,
                      tasks: list[TaskAssessment], as_of: date,
                      settings: MonitoringSettings) -> list[MilestoneAssessment]:
    by_id = {task.task_id: task for task in tasks}
    actual_by_id = {task.task_id: task for task in actual.tasks}
    results = []
    for milestone in planning.milestones:
        members = [by_id[task_id] for task_id in milestone.task_ids]
        known = [t for t in members if t.status not in ("missing", "stale")
                 and not t.actual_is_stale and t.task_id in actual_by_id]
        all_known = len(known) == len(members)
        complete = all_known and all(t.status == "completed" for t in members)
        overdue = as_of > milestone.due_date and any(t.status != "completed" for t in known)
        completed_late = complete and any(
            (actual_by_id[t.task_id].completed_on or date.min) > milestone.due_date
            for t in members
        )
        approaching = 0 <= (milestone.due_date - as_of).days <= settings.deadline_approaching_days
        if overdue or completed_late:
            status = "Delayed"
        elif complete:
            status = "Completed"
        elif not all_known or any(t.delayed for t in members) or approaching:
            status = "At Risk"
        else:
            status = "On Track"
        results.append(MilestoneAssessment(
            milestone_id=milestone.milestone_id, title=milestone.title,
            progress=(round(sum(t.actual_progress for t in members if t.actual_progress is not None) / len(members), 2)
                      if all_known and all(t.actual_progress is not None for t in members) else None),
            completed=complete, overdue=overdue, status=status,
        ))
    return results


def create_warnings(planning: PlanningSnapshot, tasks: list[TaskAssessment],
                    milestones: list[MilestoneAssessment], risk: RiskSnapshot | None,
                    as_of: date, settings: MonitoringSettings) -> list[MonitoringWarning]:
    warnings = []
    planned = {task.task_id: task for task in planning.tasks}
    for task in tasks:
        source = [f"task:{task.task_id}"]
        if task.status == "missing":
            warnings.append(MonitoringWarning(code="MISSING_ACTUAL", severity="warning",
                message=f"No actual progress record for {task.title}", task_ids=[task.task_id], source_refs=source))
            if task.planned_deadline_passed:
                warnings.append(MonitoringWarning(code="PLANNED_DEADLINE_PASSED", severity="warning",
                    message=(f"The planned deadline for {task.title} has passed; completion is unknown "
                             "because no actual record was supplied."),
                    task_ids=[task.task_id], source_refs=source))
        else:
            stale = task.status == "stale" or task.actual_is_stale
            if task.start_delay_days:
                message = f"{task.title} started {task.start_delay_days} day(s) after its planned start date"
                if stale:
                    started_on = planned[task.task_id].start_date + timedelta(days=task.start_delay_days)
                    message += f" (historical start observed on {started_on.isoformat()})"
                warnings.append(MonitoringWarning(code="LATE_START", severity="warning",
                    message=message, task_ids=[task.task_id], source_refs=source))
            if stale:
                cutoff = as_of - timedelta(days=settings.stale_after_days)
                warnings.append(MonitoringWarning(code="STALE_ACTUAL_TASK", severity="warning",
                    message=(f"The unfinished actual record for {task.title} is stale as of {as_of.isoformat()}; "
                             f"its last update predates {cutoff.isoformat()}. Current progress and completion are unknown."),
                    task_ids=[task.task_id], source_refs=source))
            else:
                if task.status == "blocked":
                    warnings.append(MonitoringWarning(code="BLOCKED_TASK", severity="warning",
                        message=f"{task.title} is blocked", task_ids=[task.task_id], source_refs=source))
                if task.overdue_days:
                    severity = "critical" if task.is_critical or planned[task.task_id].priority in ("high", "critical") else "warning"
                    warnings.append(MonitoringWarning(code="OVERDUE_TASK", severity=severity,
                        message=f"{task.title} is {task.overdue_days} day(s) overdue", task_ids=[task.task_id], source_refs=source))
                elif task.delayed:
                    warnings.append(MonitoringWarning(code="DELAYED_TASK", severity="warning",
                        message=f"{task.title} is behind schedule", task_ids=[task.task_id], source_refs=source))
    for milestone in milestones:
        if milestone.status in ("Delayed", "At Risk"):
            warnings.append(MonitoringWarning(code="MILESTONE_" + milestone.status.upper().replace(" ", "_"),
                severity="critical" if milestone.status == "Delayed" else "warning",
                message=f"Milestone {milestone.title} is {milestone.status.lower()}",
                source_refs=[f"milestone:{milestone.milestone_id}"]))
    if risk:
        for item in risk.risks:
            if item.status == "open" and item.level in ("high", "critical"):
                source_refs = [f"risk:{item.risk_id}"]
                if item.source:
                    source_refs.append(item.source)
                warnings.append(MonitoringWarning(code="HIGH_RISK", severity="warning",
                    message=f"Open {item.level} risk {item.risk_id}: {item.description}",
                    task_ids=item.task_ids, source_refs=source_refs))
    if planning.updated_on is not None and (as_of - planning.updated_on).days > settings.stale_after_days:
        warnings.append(MonitoringWarning(code="STALE_PLAN", severity="warning",
            message="Planning snapshot is stale"))

    analysis = planning.context.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    if analysis.get("deadline_met") is False:
        warnings.append(MonitoringWarning(code="BASELINE_DEADLINE_NOT_MET", severity="warning",
            message=("The baseline planning schedule does not meet the project deadline; "
                     "this is a planning risk, not an observed execution delay."),
            source_refs=["planning:analysis"]))
    # Planning returns warnings at both levels; retain each source warning once.
    seen = set()
    for source_warnings, source_ref in (
        (planning.context.get("warnings", []), "planning:warnings"),
        (analysis.get("warnings", []), "planning:analysis"),
    ):
        if isinstance(source_warnings, str):
            source_warnings = [source_warnings]
        if not isinstance(source_warnings, list):
            continue
        for message in source_warnings:
            if not isinstance(message, str) or not message.strip() or message in seen:
                continue
            seen.add(message)
            warnings.append(MonitoringWarning(code="PLANNING_WARNING", severity="warning",
                message=f"Planning Agent warning: {message}", source_refs=[source_ref]))
    return warnings


def classify_project(planning: PlanningSnapshot, tasks: list[TaskAssessment],
                     milestones: list[MilestoneAssessment], metrics: MonitoringMetrics,
                     warnings: list[MonitoringWarning], settings: MonitoringSettings) -> Literal["ON_TRACK", "AT_RISK", "DELAYED"]:
    priorities = {task.task_id: task.priority for task in planning.tasks}
    if any(t.status not in ("missing", "stale") and not t.actual_is_stale
           and t.overdue_days and (t.is_critical or priorities[t.task_id] in ("high", "critical")) for t in tasks) or any(
        m.status == "Delayed" for m in milestones
    ) or (metrics.progress_variance is not None and metrics.progress_variance <= -settings.delayed_variance):
        return "DELAYED"
    analysis = planning.context.get("analysis")
    baseline_deadline_failed = isinstance(analysis, dict) and analysis.get("deadline_met") is False
    if (metrics.total_tasks == 0 or metrics.missing_actual_tasks or metrics.stale_actual_tasks or metrics.overdue_tasks
        or metrics.delayed_tasks or metrics.blocked_tasks or metrics.high_risks or baseline_deadline_failed
        or any(t.status == "stale" or t.actual_is_stale for t in tasks)
        or any(t.status != "missing" and t.start_delay_days > 0 for t in tasks)
        or any(m.status == "At Risk" for m in milestones)
        or any(w.code in ("STALE_PLAN", "STALE_ACTUAL", "STALE_ACTUAL_TASK", "BLOCKED_TASK",
                          "MISSING_ACTUAL", "PLANNED_DEADLINE_PASSED",
                          "LATE_START", "BASELINE_DEADLINE_NOT_MET", "PLANNING_WARNING",
                          "RISK_UNAVAILABLE", "STALE_RISK") for w in warnings)
        or (metrics.progress_variance is not None and metrics.progress_variance <= -settings.at_risk_variance)):
        return "AT_RISK"
    return "ON_TRACK"
