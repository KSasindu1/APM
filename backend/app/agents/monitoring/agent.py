"""Orchestrate authorized monitoring without modifying any other agent."""

import asyncio

from .interfaces import (
    ActualDataProvider, CoordinatorReportSink, MonitoringSummarizer,
    PlanningProvider, RiskKnowledgeProvider,
)
from .milestone_monitor import assess_milestones
from .progress_calculator import assess_tasks, calculate_metrics
from .report_generator import build_report
from .schemas import (
    ActualProjectSnapshot, CoordinatorReportMessage, MonitoringPrincipal,
    MonitoringReport, MonitoringRequest, MonitoringSettings, PlanningSnapshot, RiskSnapshot,
)
from .security import require_access
from .status_analyzer import classify_project, create_warnings


class MonitoringAgent:
    def __init__(self, planning: PlanningProvider, actual: ActualDataProvider,
                 risk: RiskKnowledgeProvider | None = None,
                 summarizer: MonitoringSummarizer | None = None,
                 coordinator: CoordinatorReportSink | None = None,
                 settings: MonitoringSettings | None = None):
        self.planning = planning
        self.actual = actual
        self.risk = risk
        self.summarizer = summarizer
        self.coordinator = coordinator
        self.settings = settings or MonitoringSettings()

    async def generate(self, request: MonitoringRequest,
                       principal: MonitoringPrincipal) -> MonitoringReport:
        """Fetch, validate, calculate, summarize, then optionally publish one report."""
        require_access(principal, request.project_id, "monitoring:generate")
        timeout = self.settings.provider_timeout_seconds
        plan = PlanningSnapshot.model_validate(await asyncio.wait_for(
            self.planning.get_plan(request.project_id), timeout))
        actual = ActualProjectSnapshot.model_validate(await asyncio.wait_for(
            self.actual.get_actual(request.project_id), timeout))
        risk = None
        if self.risk is not None:
            supplied = await asyncio.wait_for(self.risk.get_risks(request.project_id), timeout)
            if supplied is not None:
                risk = RiskSnapshot.model_validate(supplied)
        self._validate_snapshots(request, plan, actual, risk)

        tasks = assess_tasks(plan, actual, request.as_of, self.settings)
        metrics = calculate_metrics(tasks, risk)
        milestones = assess_milestones(plan, actual, tasks, request.as_of, self.settings)
        warnings = create_warnings(plan, tasks, milestones, risk, request.as_of, self.settings)
        limitations = []
        if not plan.tasks:
            limitations.append("No planned tasks; project progress is unavailable")
        if metrics.missing_actual_tasks:
            limitations.append("Some planned tasks lack actual records; overall progress is unavailable")
        if (request.as_of - actual.updated_on).days > self.settings.stale_after_days:
            warnings.append(self._stale_actual_warning())
            limitations.append("Actual progress snapshot is stale")
        if risk is None:
            limitations.append("No risk snapshot supplied; risk coverage is unknown")
        elif (request.as_of - risk.updated_on).days > self.settings.stale_after_days:
            limitations.append("Risk snapshot is stale")
        status = classify_project(plan, tasks, milestones, metrics, warnings, self.settings)
        report = await build_report(request, principal.subject, status, metrics, tasks,
                                    milestones, warnings, limitations, self.settings, self.summarizer,
                                    risk_supplied=risk is not None)
        if self.coordinator is not None:
            await asyncio.wait_for(self.coordinator.publish(CoordinatorReportMessage(
                correlation_id=request.request_id, project_id=request.project_id, report=report)), timeout)
        return report

    @staticmethod
    def _stale_actual_warning():
        from .schemas import MonitoringWarning
        return MonitoringWarning(code="STALE_ACTUAL", severity="warning",
                                 message="Actual progress snapshot is stale")

    @staticmethod
    def _validate_snapshots(request: MonitoringRequest, plan: PlanningSnapshot,
                            actual: ActualProjectSnapshot, risk: RiskSnapshot | None) -> None:
        if any(snapshot.project_id != request.project_id or snapshot.updated_on > request.as_of
               for snapshot in (plan, actual) if snapshot is not None):
            raise ValueError("Snapshots must match the requested project and not postdate as_of")
        if risk is not None and (risk.project_id != request.project_id or risk.updated_on > request.as_of):
            raise ValueError("Risk snapshot must match the project and not postdate as_of")
        known = {task.task_id for task in plan.tasks}
        if not {task.task_id for task in actual.tasks} <= known:
            raise ValueError("Actual snapshot contains unknown task IDs")
        if any(task.updated_on > request.as_of for task in actual.tasks):
            raise ValueError("Actual task update postdates as_of")
        if risk and any(not set(item.task_ids) <= known for item in risk.risks):
            raise ValueError("Risk snapshot references unknown tasks")
