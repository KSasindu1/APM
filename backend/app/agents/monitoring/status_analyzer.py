"""Deterministic project classification and reason-bearing warnings."""

from datetime import date
from typing import Literal

from .schemas import (
    MilestoneAssessment, MonitoringMetrics, MonitoringSettings, MonitoringWarning,
    PlanningSnapshot, RiskSnapshot, TaskAssessment,
)


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
        if task.overdue_days:
            severity = "critical" if planned[task.task_id].priority in ("high", "critical") else "warning"
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
                warnings.append(MonitoringWarning(code="HIGH_RISK", severity="warning",
                    message=f"Open {item.level} risk {item.risk_id}", task_ids=item.task_ids,
                    source_refs=[f"risk:{item.risk_id}"]))
    if (as_of - planning.updated_on).days > settings.stale_after_days:
        warnings.append(MonitoringWarning(code="STALE_PLAN", severity="warning",
            message="Planning snapshot is stale"))
    return warnings


def classify_project(planning: PlanningSnapshot, tasks: list[TaskAssessment],
                     milestones: list[MilestoneAssessment], metrics: MonitoringMetrics,
                     warnings: list[MonitoringWarning], settings: MonitoringSettings) -> Literal["ON_TRACK", "AT_RISK", "DELAYED"]:
    priorities = {task.task_id: task.priority for task in planning.tasks}
    if any(t.overdue_days and priorities[t.task_id] in ("high", "critical") for t in tasks) or any(
        m.status == "Delayed" for m in milestones
    ) or (metrics.progress_variance is not None and metrics.progress_variance <= -settings.delayed_variance):
        return "DELAYED"
    if (metrics.total_tasks == 0 or metrics.missing_actual_tasks or metrics.overdue_tasks
        or metrics.delayed_tasks or metrics.high_risks
        or any(m.status == "At Risk" for m in milestones)
        or any(w.code in ("STALE_PLAN", "STALE_ACTUAL") for w in warnings)
        or (metrics.progress_variance is not None and metrics.progress_variance <= -settings.at_risk_variance)):
        return "AT_RISK"
    return "ON_TRACK"
