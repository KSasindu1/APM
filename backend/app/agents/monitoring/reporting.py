"""Build evidence-bound summaries and passive planning feedback from observed facts."""

import asyncio
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from .schemas import (
    MilestoneAssessment,
    MonitoringMetrics,
    MonitoringReport,
    MonitoringRequest,
    MonitoringSettings,
    MonitoringSummarizer,
    MonitoringWarning,
    PlanningFeedbackMessage,
    PlanningSnapshot,
    RiskSnapshot,
    SummaryFact,
    TaskAssessment,
)


def verified_facts(metrics: MonitoringMetrics, milestones: list[MilestoneAssessment],
                   status: str, risk_supplied: bool,
                   warnings: list[MonitoringWarning]) -> list[SummaryFact]:
    reasons = list(dict.fromkeys(w.code.replace("_", " ").lower() for w in warnings))
    if metrics.progress_variance is not None and metrics.progress_variance < 0:
        reasons.append(f"progress variance {metrics.progress_variance:+} points")
    if metrics.total_tasks == 0:
        reasons.append("no planned tasks")
    explanation = ", ".join(reasons) if reasons else "no monitoring thresholds were triggered"
    facts = [SummaryFact(fact_id="status", text=f"Project status: {status}. Reasons: {explanation}.",
                         source_refs=["monitoring:classification"])]
    if metrics.actual_progress is not None:
        facts.append(SummaryFact(fact_id="progress", text=(
            f"Actual progress is {metrics.actual_progress}% versus expected progress "
            f"of {metrics.expected_progress}% (variance {metrics.progress_variance:+} percentage points)."),
            source_refs=["planning:snapshot", "actual:snapshot"]))
    else:
        coverage = (f"Progress cannot be calculated: {metrics.missing_actual_tasks} of "
                    f"{metrics.total_tasks} planned tasks have no actual record")
        if metrics.stale_actual_tasks:
            coverage += f"; {metrics.stale_actual_tasks} task(s) have stale unfinished actual records"
        facts.append(SummaryFact(fact_id="coverage", text=coverage + ".",
            source_refs=["planning:snapshot", "actual:snapshot"]))
    facts.append(SummaryFact(fact_id="tasks", text=(
        f"{metrics.overdue_tasks} task(s) are overdue and {metrics.delayed_tasks} are delayed."),
        source_refs=["planning:snapshot", "actual:snapshot"]))
    facts.append(SummaryFact(fact_id="milestones", text=(
        f"{sum(m.status == 'Delayed' for m in milestones)} milestone(s) are delayed and "
        f"{sum(m.status == 'At Risk' for m in milestones)} are at risk."),
        source_refs=[f"milestone:{m.milestone_id}" for m in milestones]))
    if risk_supplied:
        facts.append(SummaryFact(fact_id="risks", text=f"{metrics.high_risks} open high/critical risk(s) were supplied.",
            source_refs=["risk:snapshot"]))
    return facts


def _planning_feedback(request: MonitoringRequest, tasks: list[TaskAssessment],
                       planning: PlanningSnapshot | None) -> list[PlanningFeedbackMessage]:
    children: dict[str, list[str]] = {}
    if planning is not None:
        children = {task.task_id: [] for task in planning.tasks}
        for task in planning.tasks:
            for parent in task.dependency_ids:
                children[parent].append(task.task_id)
    messages = []
    for task in tasks:
        if task.status == "missing":
            continue
        stale = task.status == "stale" or task.actual_is_stale
        if not stale and task.actual_progress is None:
            continue
        delays = [("late_start", task.start_delay_days)]
        if not stale:
            if task.status == "completed":
                delays.append(("completed_late", task.completed_late_days))
            else:
                delays.append(("overdue", task.overdue_days))
        delays = [(reason, days) for reason, days in delays if days > 0]
        if not delays:
            continue

        affected = set()
        pending = list(children.get(task.task_id, []))
        while pending:
            child = pending.pop()
            if child not in affected:
                affected.add(child)
                pending.extend(children.get(child, []))
        affected_tasks: list[JsonValue] = ([planned.task_id for planned in planning.tasks if planned.task_id in affected]
                          if planning is not None else [])
        # These are report artifacts, not API calls: Planning currently only supports CREATE_SCHEDULE.
        for reason, delay_days in delays:
            messages.append(PlanningFeedbackMessage(
                sender="project_monitor_report_agent", receiver="planning_agent",
                action="SCHEDULE_DELAY_DETECTED", request_id=request.request_id,
                data={
                    "project_id": request.project_id,
                    "task_id": task.task_id,
                    "delay_days": delay_days,
                    "reason": reason,
                    "affected_tasks": affected_tasks,
                },
            ))
    return messages


async def build_report(request: MonitoringRequest, requested_by: str,
                       status: Literal["ON_TRACK", "AT_RISK", "DELAYED"],
                       metrics: MonitoringMetrics, tasks: list[TaskAssessment],
                       milestones: list[MilestoneAssessment], warnings: list[MonitoringWarning],
                       limitations: list[str], settings: MonitoringSettings,
                       summarizer: MonitoringSummarizer | None = None,
                       risk_supplied: bool = False,
                       risk: RiskSnapshot | None = None,
                       planning: PlanningSnapshot | None = None) -> MonitoringReport:
    facts = verified_facts(metrics, milestones, status, risk_supplied, warnings)
    late_starts = [task for task in tasks if task.status != "missing" and task.start_delay_days > 0
                   and (task.actual_progress is not None or task.status == "stale" or task.actual_is_stale)]
    if late_starts:
        facts.append(SummaryFact(fact_id="late_start", text=(
            f"{len(late_starts)} task(s) have observed late starts: "
            + "; ".join(f"{task.title} ({task.start_delay_days} day(s) after its planned start date)"
                        for task in late_starts) + "."),
            source_refs=[f"task:{task.task_id}" for task in late_starts]))
    selected = [fact.fact_id for fact in facts]
    method = "deterministic_extractive"
    if summarizer is not None:
        try:
            selector_facts = [fact.model_copy(deep=True) for fact in facts]
            selection = await asyncio.wait_for(summarizer.select_facts(selector_facts), settings.provider_timeout_seconds)
            allowed = set(selected)
            if ("status" not in selection.fact_ids or len(set(selection.fact_ids)) != len(selection.fact_ids)
                    or not set(selection.fact_ids) <= allowed):
                raise ValueError("Summarizer selected invalid fact IDs")
            selected = selection.fact_ids
            method = "llm_extractive"
        except Exception:  # noqa: BLE001 - Any selector failure must retain a deterministic report.
            limitations.append("AI fact selection unavailable or invalid; deterministic summary used")
    by_id = {fact.fact_id: fact for fact in facts}
    summary = ("AI-generated fact selection (verify with project manager): " if method == "llm_extractive"
               else "Automated monitoring summary: ") + " ".join(by_id[fact_id].text for fact_id in selected)
    actions = []
    if status != "ON_TRACK":
        actions.append("Project manager: review the flagged tasks, milestones, and risks before deciding next steps.")
    if risk is not None:
        for item in risk.risks:
            if item.status == "open" and item.recommended_action:
                actions.append(f"Risk Agent suggestion ({item.risk_id}): {item.recommended_action}")
    return MonitoringReport(
        report_id=uuid4().hex, request_id=request.request_id,
        project_id=request.project_id, requested_by=requested_by,
        generated_at=datetime.now(timezone.utc), as_of=request.as_of,
        status=status, metrics=metrics, tasks=tasks, milestones=milestones,
        warnings=warnings, retrieved_evidence=[], facts=facts,
        summary_fact_ids=selected, summary=summary, summary_method=method,
        limitations=limitations, suggested_actions=actions,
        risks=risk.risks if risk is not None else [],
        risk_summary=risk.summary if risk is not None else "",
        risk_evidence_used=risk.evidence_used if risk is not None else [],
        risk_knowledge_sources=risk.knowledge_sources if risk is not None else [],
        planning_context=planning.context if planning is not None else {},
        planning_feedback=_planning_feedback(request, tasks, planning),
    )
