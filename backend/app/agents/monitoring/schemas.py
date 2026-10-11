"""Version 1 contracts owned by the Monitoring and Reporting Agent.

Dates are project-local calendar dates; percentages use the 0..100 scale.
"""

from datetime import date, datetime, timedelta
import re
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, StrictBool,
    field_validator, model_serializer, model_validator,
)

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
Percentage = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class MonitoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, revalidate_instances="always")


class PlannedTask(MonitoringModel):
    task_id: Identifier
    title: str = Field(min_length=1, max_length=300)
    start_date: date
    due_date: date
    weight: float = Field(default=1, gt=0, le=100000, allow_inf_nan=False)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    is_critical: bool = Field(default=False, strict=True)
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
    updated_on: date | None = None
    fetched_on: date | None = None
    tasks: list[PlannedTask] = Field(default_factory=list, max_length=10000)
    milestones: list[PlannedMilestone] = Field(default_factory=list, max_length=1000)
    context: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("context", mode="before")
    @classmethod
    def preserve_analysis_references(cls, value):
        if isinstance(value, dict) and isinstance(value.get("analysis"), dict):
            path = value["analysis"].get("critical_path")
            if isinstance(path, list) and any(isinstance(ref, str) and ref != ref.strip() for ref in path):
                raise ValueError("Planning critical_path task IDs must not be silently normalized")
        return value

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
        if "analysis" in self.context:
            analysis = self.context["analysis"]
            if not isinstance(analysis, dict):
                raise ValueError("Planning analysis must be an object")
            if "deadline_met" in analysis and not isinstance(analysis["deadline_met"], bool):
                raise ValueError("Planning analysis deadline_met must be a boolean")
            if "critical_path" in analysis:
                path = analysis["critical_path"]
                if (not isinstance(path, list) or any(not isinstance(ref, str) for ref in path)
                        or len(set(path)) != len(path) or not set(path) <= task_ids):
                    raise ValueError("Planning critical_path must contain unique known task IDs")
                for task in self.tasks:
                    if "is_critical" not in task.model_fields_set and task.task_id in path:
                        task.is_critical = True
        if (self.updated_on is not None and self.fetched_on is not None
                and self.updated_on > self.fetched_on):
            raise ValueError("Planning update cannot postdate its observation")
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
    actual_start: date | None = None

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
        if self.actual_start is not None:
            if self.status == "not_started" or self.actual_start > self.updated_on:
                raise ValueError("Actual start must belong to a started task and not postdate its update")
            if self.completed_on is not None and self.actual_start > self.completed_on:
                raise ValueError("Actual start cannot be after completion")
        return self


def _calendar_date(value):
    if type(value) is date or isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    raise ValueError("Calendar dates must use YYYY-MM-DD")


CalendarDate = Annotated[date, BeforeValidator(_calendar_date)]


class ActualProgressTaskInput(MonitoringModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    task_id: Identifier
    status: Literal["NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"]
    progress_percentage: Percentage = Field(strict=True)
    actual_start: CalendarDate | None = None
    actual_end: CalendarDate | None = None
    updated_on: CalendarDate | None = None

    @field_validator("status", mode="before")
    @classmethod
    def canonical_status(cls, value):
        if isinstance(value, str):
            value = value.strip().upper().replace(" ", "_")
            return "NOT_STARTED" if value == "PLANNED" else value
        return value


class ActualProgressPayload(MonitoringModel):
    """Only recognized execution fields; never add observation dates as source updates."""

    model_config = ConfigDict(str_strip_whitespace=False)

    project_id: Identifier
    tasks: list[ActualProgressTaskInput] = Field(min_length=1, max_length=1000)
    updated_on: CalendarDate | None = None

    def canonical_payload(self) -> dict:
        return self.model_dump(mode="json", exclude_unset=True, exclude_none=True)


class ObservationRequest(MonitoringModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    request_id: Identifier
    source: Identifier
    observed_on: CalendarDate
    actual_progress: ActualProgressPayload
    save_observation: StrictBool = False
    planning_message: dict[str, JsonValue] | None = None
    received_on: CalendarDate | None = None

    @model_validator(mode="after")
    def paired_planning_message(self):
        if (self.planning_message is None) != (self.received_on is None):
            raise ValueError("A Planning message requires its receipt date")
        return self


class ObservationReceipt(MonitoringModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    observation_id: Identifier
    request_id: Identifier
    project_id: Identifier
    source: Identifier
    observed_on: CalendarDate
    accepted_at: datetime
    submitted_by: Identifier
    actual_progress: dict[str, JsonValue]
    persisted: StrictBool
    limitations: list[str] = Field(max_length=20)

    @field_validator("accepted_at", mode="before")
    @classmethod
    def timestamp_type(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("Acceptance requires an explicit UTC timestamp")
        return value

    @model_validator(mode="after")
    def bound_observation(self):
        # Use the same adapter as ActualProgressProvider, including completion,
        # duplicate-task and source-date consistency checks, on stored receipts too.
        from .providers import adapt_actual_progress

        payload = ActualProgressPayload.model_validate(self.actual_progress).canonical_payload()
        snapshot = adapt_actual_progress(payload, self.observed_on)
        if snapshot.project_id != self.project_id:
            raise ValueError("Receipt actuals must belong to its project")
        if self.accepted_at.utcoffset() != timedelta(0):
            raise ValueError("Acceptance must be an explicit UTC timestamp")
        # Project-local observation dates are not UTC acceptance dates. Their
        # consistency is checked against source dates/as_of, never acceptance time.
        if self.observed_on > date.today():
            raise ValueError("Observations cannot be future-dated")
        self.actual_progress = payload
        return self


class KnowledgeDocument(MonitoringModel):
    document_id: Identifier
    project_id: Identifier
    source: Literal["project_update", "risk_knowledge", "previous_report"]
    recorded_on: date
    text: str = Field(min_length=1, max_length=12000)


class ActualProjectSnapshot(MonitoringModel):
    project_id: Identifier
    updated_on: date | None = None
    observed_on: date | None = None
    tasks: list[ActualTask] = Field(default_factory=list, max_length=10000)
    updates: list[KnowledgeDocument] = Field(default_factory=list, max_length=1000)
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_records(self):
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate actual task IDs")
        if self.updated_on is None:
            if self.tasks or self.updates:
                raise ValueError("Actual records require a snapshot observation date")
            return self
        if self.observed_on is not None and self.updated_on > self.observed_on:
            raise ValueError("Actual snapshot cannot postdate its observation")
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
    risk: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    evidence: str | list[str] = Field(default_factory=list)
    source: str | None = None
    affected_area: str | None = None
    recommended_action: str | None = None
    impact_days: int | None = Field(default=None, ge=0)


class RiskSnapshot(MonitoringModel):
    project_id: Identifier
    updated_on: date
    observed_on: date | None = None
    risks: list[ProjectRisk] = Field(default_factory=list, max_length=1000)
    knowledge: list[KnowledgeDocument] = Field(default_factory=list, max_length=1000)
    summary: str = ""
    evidence_used: list[JsonValue] = Field(default_factory=list)
    knowledge_sources: list[JsonValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_records(self):
        if self.observed_on is not None and self.updated_on > self.observed_on:
            raise ValueError("Risk snapshot cannot postdate its observation")
        if len({r.risk_id for r in self.risks}) != len(self.risks):
            raise ValueError("Duplicate risk IDs")
        if any(d.project_id != self.project_id or d.recorded_on > self.updated_on for d in self.knowledge):
            raise ValueError("Knowledge must belong to this project and snapshot date")
        return self



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
    risk_failure_policy: Literal["fail", "report_unknown"] = "fail"
    risk_context_max_chars: int = Field(default=100000, ge=1000, le=1000000, strict=True)


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
    start_delay_days: int = 0
    planned_deadline_passed: bool = False
    actual_is_stale: bool = False
    last_observed_progress: float | None = None
    is_critical: bool = Field(default=False, strict=True)
    planned_start: date | None = None
    due_date: date | None = None
    actual_start: date | None = None
    completed_on: date | None = None
    last_updated_on: date | None = None


class MilestoneAssessment(MonitoringModel):
    milestone_id: str
    title: str
    progress: float | None
    completed: bool
    overdue: bool
    status: Literal["Completed", "On Track", "At Risk", "Delayed"]


class MonitoringMetrics(MonitoringModel):
    stale_actual_tasks: int = 0
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


class PlanningFeedbackMessage(MonitoringModel):
    sender: Literal["project_monitor_report_agent"] = "project_monitor_report_agent"
    receiver: Literal["planning_agent"] = "planning_agent"
    action: Literal["SCHEDULE_DELAY_DETECTED"] = "SCHEDULE_DELAY_DETECTED"
    request_id: str
    data: dict[str, JsonValue]


class MonitoringCoverage(MonitoringModel):
    planning_status: Literal["available"] = "available"
    planning_freshness: Literal["known", "unknown", "stale"]
    actual_status: Literal["complete", "partial", "missing", "stale"]
    risk_status: Literal["available", "not_supplied", "stale", "unavailable"]
    milestones_status: Literal["available", "not_supplied"]
    total_tasks: int = Field(ge=0)
    fresh_actual_tasks: int = Field(ge=0)
    missing_actual_tasks: int = Field(ge=0)
    stale_actual_tasks: int = Field(ge=0)
    planning_updated_on: date | None = None
    planning_observed_on: date | None = None
    actual_updated_on: date | None = None
    actual_observed_on: date | None = None
    risk_observed_on: date | None = None
    expected_progress_method: Literal["calendar_estimate", "supplied_target", "mixed", "unavailable"]
    actual_progress_method: Literal["unweighted_task_average"] = "unweighted_task_average"


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
    risks: list[ProjectRisk] = Field(default_factory=list)
    risk_summary: str = ""
    risk_evidence_used: list[JsonValue] = Field(default_factory=list)
    risk_knowledge_sources: list[JsonValue] = Field(default_factory=list)
    planning_context: dict[str, JsonValue] = Field(default_factory=dict)
    actual_context: dict[str, JsonValue] = Field(default_factory=dict)
    coverage: MonitoringCoverage | None = None
    planning_feedback: list[PlanningFeedbackMessage] = Field(default_factory=list)
    human_review_required: Literal[True] = True

    @field_validator("planning_context", "actual_context", "risk_evidence_used", "risk_knowledge_sources")
    @classmethod
    def redact_source_credentials(cls, value):
        from .providers import sanitize_risk_context

        return sanitize_risk_context(value)

    @model_serializer(mode="wrap")
    def serialize_without_credentials(self, handler):
        # Serialization also protects legacy or subsequently mutated models,
        # including free-form metadata outside the source-context fields.
        from .providers import sanitize_risk_context

        return sanitize_risk_context(handler(self))


class CoordinatorReportMessage(MonitoringModel):
    message_type: Literal["monitoring.report.ready"] = "monitoring.report.ready"
    sender: Literal["monitoring_reporting"] = "monitoring_reporting"
    recipient: Literal["coordinator"] = "coordinator"
    correlation_id: str
    project_id: str
    report: MonitoringReport


class PlanningProvider(Protocol):
    async def get_plan(self, project_id: str) -> PlanningSnapshot: ...


class ActualDataProvider(Protocol):
    async def get_actual(self, project_id: str) -> ActualProjectSnapshot: ...


class RiskKnowledgeProvider(Protocol):
    async def get_risks(self, project_id: str) -> RiskSnapshot | None: ...


class MonitoringReportRepository(Protocol):
    async def save(self, report: MonitoringReport, principal: MonitoringPrincipal) -> None: ...
    async def get(self, project_id: str, report_id: str,
                  principal: MonitoringPrincipal) -> MonitoringReport | None: ...
    async def list(self, project_id: str, principal: MonitoringPrincipal,
                   limit: int = 20) -> list[MonitoringReport]: ...


class MonitoringObservationRepository(Protocol):
    async def save(self, receipt: ObservationReceipt, principal: MonitoringPrincipal) -> None: ...
    async def get(self, project_id: str, observation_id: str,
                  principal: MonitoringPrincipal) -> ObservationReceipt | None: ...
    async def list(self, project_id: str, principal: MonitoringPrincipal,
                   limit: int = 20) -> list[ObservationReceipt]: ...


class MonitoringSummarizer(Protocol):
    async def select_facts(self, facts: list[SummaryFact]) -> SummarySelection: ...


class CoordinatorReportSink(Protocol):
    async def publish(self, message: CoordinatorReportMessage) -> None: ...
