"""Authorized Monitoring orchestration for real Planning, actual-progress, and Risk inputs."""

import asyncio
import hashlib
import secrets

from .analysis import assess_milestones, assess_tasks, calculate_metrics, classify_project, create_warnings
from .reporting import build_report
from .schemas import (
    ActualDataProvider, ActualProjectSnapshot, CoordinatorReportMessage, CoordinatorReportSink,
    MonitoringPrincipal, MonitoringReport, MonitoringReportRepository, MonitoringRequest,
    MonitoringSettings, MonitoringSummarizer, MonitoringWarning, PlanningProvider,
    PlanningSnapshot, RiskKnowledgeProvider, RiskSnapshot,
)


class MonitoringAccessDenied(PermissionError):
    pass


def require_access(principal: MonitoringPrincipal, project_id: str, permission: str) -> None:
    if project_id not in principal.project_ids or permission not in principal.permissions:
        raise MonitoringAccessDenied("Insufficient monitoring project access")


class MonitoringTokenAuthenticator:
    """Optional server-side token mapping until the team's verified identity dependency is ready."""

    def __init__(self, identities: dict[str, MonitoringPrincipal]):
        if not identities or any(len(token) < 32 for token in identities):
            raise ValueError("Configure at least one bearer token of 32 or more characters")
        self._identities = [
            (hashlib.sha256(token.encode()).digest(), principal.model_copy(deep=True))
            for token, principal in identities.items()
        ]

    def authenticate(self, token: str) -> MonitoringPrincipal | None:
        digest = hashlib.sha256(token.encode()).digest()
        for expected, principal in self._identities:
            if secrets.compare_digest(digest, expected):
                return principal.model_copy(deep=True)
        return None


class MonitoringAgent:
    def __init__(self, planning: PlanningProvider, actual: ActualDataProvider | None = None,
                 risk: RiskKnowledgeProvider | None = None,
                 summarizer: MonitoringSummarizer | None = None,
                 coordinator: CoordinatorReportSink | None = None,
                 settings: MonitoringSettings | None = None,
                 repository: MonitoringReportRepository | None = None):
        self.planning = planning
        self.actual = actual
        self.risk = risk
        self.summarizer = summarizer
        self.coordinator = coordinator
        self.settings = settings or MonitoringSettings()
        self.repository = repository

    async def generate(self, request: MonitoringRequest,
                       principal: MonitoringPrincipal) -> MonitoringReport:
        """Read inputs, assess observed data, and optionally persist/publish one report."""
        require_access(principal, request.project_id, "monitoring:generate")
        timeout = self.settings.provider_timeout_seconds
        plan = PlanningSnapshot.model_validate(await asyncio.wait_for(
            self.planning.get_plan(request.project_id), timeout))
        actual = ActualProjectSnapshot(project_id=request.project_id)
        if self.actual is not None:
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
        limitations = list(plan.limitations)
        if plan.updated_on is None and not any("freshness" in item for item in limitations):
            limitations.append("Planning source update date is unavailable; baseline freshness is unknown")
        if not plan.tasks:
            limitations.append("No planned tasks; project progress is unavailable")
        if self.actual is None:
            limitations.append("No actual-progress source supplied; Planning statuses are not execution measurements")
        if metrics.missing_actual_tasks:
            limitations.append("Some planned tasks lack actual records; overall progress is unavailable")
        if metrics.stale_actual_tasks:
            limitations.append("Some unfinished task observations are stale; current progress and delays are unknown")
        if actual.updated_on is not None and (request.as_of - actual.updated_on).days > self.settings.stale_after_days:
            warnings.append(MonitoringWarning(code="STALE_ACTUAL", severity="warning",
                                              message="Actual progress snapshot is stale"))
            limitations.append("Actual progress snapshot is stale")
        if risk is None:
            limitations.append("No risk snapshot supplied; risk coverage is unknown")
        elif (request.as_of - risk.updated_on).days > self.settings.stale_after_days:
            limitations.append("Risk snapshot is stale")
        status = classify_project(plan, tasks, milestones, metrics, warnings, self.settings)
        report = await build_report(
            request, principal.subject, status, metrics, tasks, milestones, warnings, limitations,
            self.settings, self.summarizer, risk_supplied=risk is not None, risk=risk, planning=plan,
        )
        if self.repository is not None:
            await asyncio.wait_for(self.repository.save(report, principal), timeout)
        if self.coordinator is not None:
            await asyncio.wait_for(self.coordinator.publish(CoordinatorReportMessage(
                correlation_id=request.request_id, project_id=request.project_id, report=report)), timeout)
        return report

    async def get_report(self, project_id: str, report_id: str,
                         principal: MonitoringPrincipal) -> MonitoringReport | None:
        require_access(principal, project_id, "monitoring:read")
        if self.repository is None:
            raise RuntimeError("Monitoring report repository is not configured")
        return await asyncio.wait_for(self.repository.get(project_id, report_id, principal),
                                      self.settings.provider_timeout_seconds)

    async def list_reports(self, project_id: str, principal: MonitoringPrincipal,
                           limit: int = 20) -> list[MonitoringReport]:
        require_access(principal, project_id, "monitoring:read")
        if self.repository is None:
            raise RuntimeError("Monitoring report repository is not configured")
        return await asyncio.wait_for(self.repository.list(project_id, principal, limit),
                                      self.settings.provider_timeout_seconds)

    @staticmethod
    def _validate_snapshots(request: MonitoringRequest, plan: PlanningSnapshot,
                            actual: ActualProjectSnapshot, risk: RiskSnapshot | None) -> None:
        for snapshot in (plan, actual):
            if snapshot.project_id != request.project_id or (
                snapshot.updated_on is not None and snapshot.updated_on > request.as_of
            ):
                raise ValueError("Snapshots must match the requested project and not postdate as_of")
        if plan.fetched_on is not None and plan.fetched_on > request.as_of:
            raise ValueError("A live Planning observation cannot stand in for a historical baseline")
        if actual.observed_on is not None and actual.observed_on > request.as_of:
            raise ValueError("A later actual observation cannot stand in for historical progress")
        if risk is not None and (risk.project_id != request.project_id or risk.updated_on > request.as_of):
            raise ValueError("Risk snapshot must match the project and not postdate as_of")
        known = {task.task_id for task in plan.tasks}
        if not {task.task_id for task in actual.tasks} <= known:
            raise ValueError("Actual snapshot contains unknown task IDs")
        if any(task.updated_on > request.as_of for task in actual.tasks):
            raise ValueError("Actual task update postdates as_of")
        if risk and any(not set(item.task_ids) <= known for item in risk.risks):
            raise ValueError("Risk snapshot references unknown tasks")
