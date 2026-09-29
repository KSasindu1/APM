"""Build evidence-bound summaries after all deterministic calculations are done."""

import asyncio
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from .interfaces import MonitoringSummarizer
from .schemas import (
    MilestoneAssessment, MonitoringMetrics, MonitoringReport, MonitoringRequest,
    MonitoringSettings, MonitoringWarning, SummaryFact, TaskAssessment,
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
        facts.append(SummaryFact(fact_id="coverage", text=(
            f"Progress cannot be calculated: {metrics.missing_actual_tasks} of "
            f"{metrics.total_tasks} planned tasks have no actual record."),
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


async def build_report(request: MonitoringRequest, requested_by: str,
                       status: Literal["ON_TRACK", "AT_RISK", "DELAYED"],
                       metrics: MonitoringMetrics, tasks: list[TaskAssessment],
                       milestones: list[MilestoneAssessment], warnings: list[MonitoringWarning],
                       limitations: list[str], settings: MonitoringSettings,
                       summarizer: MonitoringSummarizer | None = None,
                       risk_supplied: bool = False) -> MonitoringReport:
    facts = verified_facts(metrics, milestones, status, risk_supplied, warnings)
    selected = [fact.fact_id for fact in facts]
    method = "deterministic_extractive"
    if summarizer is not None:
        try:
            selection = await asyncio.wait_for(summarizer.select_facts(facts), settings.provider_timeout_seconds)
            allowed = set(selected)
            if ("status" not in selection.fact_ids or len(set(selection.fact_ids)) != len(selection.fact_ids)
                    or not set(selection.fact_ids) <= allowed):
                raise ValueError("Summarizer selected invalid fact IDs")
            selected = selection.fact_ids
            method = "llm_extractive"
        except Exception:
            limitations.append("AI fact selection unavailable or invalid; deterministic summary used")
    by_id = {fact.fact_id: fact for fact in facts}
    summary = ("AI-generated fact selection (verify with project manager): " if method == "llm_extractive"
               else "Automated monitoring summary: ") + " ".join(by_id[fact_id].text for fact_id in selected)
    actions = []
    if status != "ON_TRACK":
        actions.append("Project manager: review the flagged tasks, milestones, and risks before deciding next steps.")
    return MonitoringReport(
        report_id=uuid4().hex, request_id=request.request_id,
        project_id=request.project_id, requested_by=requested_by,
        generated_at=datetime.now(timezone.utc), as_of=request.as_of,
        status=status, metrics=metrics, tasks=tasks, milestones=milestones,
        warnings=warnings, retrieved_evidence=[], facts=facts,
        summary_fact_ids=selected, summary=summary, summary_method=method,
        limitations=limitations, suggested_actions=actions,
    )
