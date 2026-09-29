"""Implement these adapters when the other team members' services are ready."""

from typing import Protocol

from .schemas import (
    ActualProjectSnapshot, CoordinatorReportMessage, MonitoringReport,
    PlanningSnapshot, RiskSnapshot, SummaryFact, SummarySelection,
)


class PlanningProvider(Protocol):
    async def get_plan(self, project_id: str) -> PlanningSnapshot: ...


class ActualDataProvider(Protocol):
    async def get_actual(self, project_id: str) -> ActualProjectSnapshot: ...


class RiskKnowledgeProvider(Protocol):
    async def get_risks(self, project_id: str) -> RiskSnapshot | None: ...


class MonitoringReportRepository(Protocol):
    def save(self, report: MonitoringReport) -> None: ...
    def get(self, project_id: str, report_id: str) -> MonitoringReport | None: ...
    def list(self, project_id: str, limit: int = 20) -> list[MonitoringReport]: ...


class MonitoringSummarizer(Protocol):
    async def select_facts(self, facts: list[SummaryFact]) -> SummarySelection: ...


class CoordinatorReportSink(Protocol):
    """Optional future internal agent bus. No transport is assumed here."""

    async def publish(self, message: CoordinatorReportMessage) -> None: ...
