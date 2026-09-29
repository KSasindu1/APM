"""Version 1 contracts owned by the Monitoring and Reporting Agent.

Dates are project-local calendar dates; percentages use the 0..100 scale.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
Percentage = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class MonitoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlannedTask(MonitoringModel):
    task_id: Identifier
    title: str = Field(min_length=1, max_length=300)
    start_date: date
    due_date: date
    weight: float = Field(default=1, gt=0, le=100000, allow_inf_nan=False)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    expected_progress: Percentage | None = None
    dependency_ids: list[Identifier] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.due_date < self.start_date:
            raise ValueError("due_date must be on or after start_date")
        if self.task_id in self.dependency_ids or len(set(self.dependency_ids)) != len(self.dependency_ids):
            raise ValueError("Dependencies must be unique and cannot reference the task itself")
        return self


class PlannedMilestone(MonitoringModel):
    milestone_id: Identifier
    title: str = Field(min_length=1, max_length=300)
    due_date: date
    task_ids: list[Identifier] = Field(min_length=1, max_length=10000)


class PlanningSnapshot(MonitoringModel):
    project_id: Identifier
    updated_on: date
    tasks: list[PlannedTask] = Field(default_factory=list, max_length=10000)
    milestones: list[PlannedMilestone] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_references(self):
        task_ids = {task.task_id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("Duplicate planned task IDs")
        if len({m.milestone_id for m in self.milestones}) != len(self.milestones):
            raise ValueError("Duplicate milestone IDs")
        pending = {}
        children = {task_id: [] for task_id in task_ids}
        for task in self.tasks:
            if not set(task.dependency_ids) <= task_ids:
                raise ValueError("Unknown task dependency")
            pending[task.task_id] = len(task.dependency_ids)
            for parent in task.dependency_ids:
                children[parent].append(task.task_id)
        ready = [key for key, count in pending.items() if not count]
        visited = 0
        while ready:
            visited += 1
            for child in children[ready.pop()]:
                pending[child] -= 1
                if not pending[child]:
                    ready.append(child)
        if visited != len(task_ids):
            raise ValueError("Task dependency cycle")
        for milestone in self.milestones:
            if not set(milestone.task_ids) <= task_ids or len(set(milestone.task_ids)) != len(milestone.task_ids):
                raise ValueError("Milestone references must be unique known tasks")
        return self


class ActualTask(MonitoringModel):
    task_id: Identifier
    status: Literal["not_started", "in_progress", "blocked", "completed"]
    progress: Percentage
    updated_on: date
    completed_on: date | None = None

    @model_validator(mode="after")
    def consistent_completion(self):
        if self.status == "completed":
            if self.progress != 100 or self.completed_on is None:
                raise ValueError("Completed tasks require 100% progress and completed_on")
            if self.completed_on > self.updated_on:
                raise ValueError("Completion cannot be after the update date")
        elif self.completed_on is not None or self.progress == 100:
            raise ValueError("Only completed tasks can have completed_on or 100% progress")
        if self.status == "not_started" and self.progress != 0:
            raise ValueError("Not-started tasks must have zero progress")
        return self


class KnowledgeDocument(MonitoringModel):
    document_id: Identifier
    project_id: Identifier
    source: Literal["project_update", "risk_knowledge", "previous_report"]
    recorded_on: date
    text: str = Field(min_length=1, max_length=12000)


class ActualProjectSnapshot(MonitoringModel):
    project_id: Identifier
    updated_on: date
    tasks: list[ActualTask] = Field(default_factory=list, max_length=10000)
    updates: list[KnowledgeDocument] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def consistent_records(self):
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate actual task IDs")
        if any(t.updated_on > self.updated_on for t in self.tasks):
            raise ValueError("Task update cannot be newer than its snapshot")
        if any(d.project_id != self.project_id or d.recorded_on > self.updated_on for d in self.updates):
            raise ValueError("Updates must belong to this project and snapshot date")
        return self


class ProjectRisk(MonitoringModel):
    risk_id: Identifier
    description: str = Field(min_length=1, max_length=2000)
    level: Literal["low", "medium", "high", "critical"]
    status: Literal["open", "mitigated", "closed"] = "open"
    task_ids: list[Identifier] = Field(default_factory=list)


class RiskSnapshot(MonitoringModel):
    project_id: Identifier
    updated_on: date
    risks: list[ProjectRisk] = Field(default_factory=list, max_length=1000)
    knowledge: list[KnowledgeDocument] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def consistent_records(self):
        if len({r.risk_id for r in self.risks}) != len(self.risks):
            raise ValueError("Duplicate risk IDs")
        if any(d.project_id != self.project_id or d.recorded_on > self.updated_on for d in self.knowledge):
            raise ValueError("Knowledge must belong to this project and snapshot date")
        return self


class MonitoringFixture(MonitoringModel):
    planning: PlanningSnapshot
    actual: ActualProjectSnapshot
    risk: RiskSnapshot | None = None


class MonitoringRequest(MonitoringModel):
    project_id: Identifier
    request_id: Identifier
    as_of: date = Field(default_factory=date.today)
    query: str = Field(default="project progress delays deadlines milestones risks issues", min_length=1, max_length=500)


class MonitoringPrincipal(MonitoringModel):
    """Trusted server-side identity, never accepted from a report request body."""

    subject: Identifier
    project_ids: frozenset[Identifier]
    permissions: frozenset[Literal["monitoring:read", "monitoring:generate"]]


class MonitoringSettings(MonitoringModel):
    delay_threshold: float = Field(default=10, gt=0, le=100, allow_inf_nan=False)
    at_risk_variance: float = Field(default=10, gt=0, le=100, allow_inf_nan=False)
    delayed_variance: float = Field(default=25, gt=0, le=100, allow_inf_nan=False)
    deadline_approaching_days: int = Field(default=3, ge=0)
    stale_after_days: int = Field(default=7, ge=1)
    retrieval_limit: int = Field(default=5, ge=1, le=20)
    provider_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)


class MonitoringWarning(MonitoringModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    message: str
    task_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class TaskAssessment(MonitoringModel):
    task_id: str
    title: str
    status: str
    actual_progress: float | None
    expected_progress: float
    variance: float | None
    overdue_days: int
    completed_late_days: int
    delayed: bool = False
    dependency_delayed: bool = False


class MilestoneAssessment(MonitoringModel):
    milestone_id: str
    title: str
    progress: float | None
    completed: bool
    overdue: bool
    status: Literal["Completed", "On Track", "At Risk", "Delayed"]


class MonitoringMetrics(MonitoringModel):
    total_tasks: int
    completed_tasks: int
    missing_actual_tasks: int
    overdue_tasks: int
    blocked_tasks: int
    in_progress_tasks: int
    not_started_tasks: int
    delayed_tasks: int
    high_risks: int
    actual_progress: float | None
    expected_progress: float | None
    progress_variance: float | None
    actual_progress_is_lower_bound: bool


class RetrievedEvidence(MonitoringModel):
    document_id: str
    source: str
    recorded_on: date
    excerpt: str
    score: float


class SummaryFact(MonitoringModel):
    fact_id: str
    text: str
    source_refs: list[str]


class SummarySelection(MonitoringModel):
    fact_ids: list[str] = Field(min_length=1, max_length=8)


class MonitoringReport(MonitoringModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: str
    request_id: str
    project_id: str
    requested_by: str
    generated_at: datetime
    as_of: date
    status: Literal["ON_TRACK", "AT_RISK", "DELAYED"]
    metrics: MonitoringMetrics
    tasks: list[TaskAssessment]
    milestones: list[MilestoneAssessment]
    warnings: list[MonitoringWarning]
    retrieved_evidence: list[RetrievedEvidence]
    facts: list[SummaryFact]
    summary_fact_ids: list[str]
    summary: str
    summary_method: Literal["deterministic_extractive", "llm_extractive"]
    limitations: list[str]
    suggested_actions: list[str]
    human_review_required: Literal[True] = True


class CoordinatorReportMessage(MonitoringModel):
    message_type: Literal["monitoring.report.ready"] = "monitoring.report.ready"
    sender: Literal["monitoring_reporting"] = "monitoring_reporting"
    recipient: Literal["coordinator"] = "coordinator"
    correlation_id: str
    project_id: str
    report: MonitoringReport
