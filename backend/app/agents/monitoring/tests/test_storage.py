"""Offline persistence and agent integration tests; all credentials are synthetic."""

import asyncio
import json
import traceback
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from app.agents.monitoring import storage
from app.agents.monitoring.agent import MonitoringAccessDenied, MonitoringAgent
from app.agents.monitoring.schemas import (
    MonitoringPrincipal,
    MonitoringReport,
    MonitoringRequest,
    ObservationReceipt,
    RiskSnapshot,
)
from app.agents.monitoring.storage import (
    SupabaseMonitoringConfigurationError,
    SupabaseMonitoringError,
    SupabaseMonitoringReportRepository,
    SupabaseMonitoringObservationRepository,
    SupabaseMonitoringSettings,
)

from .conftest import FixtureProviders


BASE_URL = "https://example.supabase.co"
FAKE_KEY = "sb_secret_mock_not_a_real_credential"
PRINCIPAL = MonitoringPrincipal(
    subject="manager", project_ids=frozenset({"P001"}),
    permissions=frozenset({"monitoring:read", "monitoring:generate"}),
)
SELECT = "report_id,project_id,request_id,generated_at,report"


def monitoring_request():
    return MonitoringRequest(project_id="P001", request_id="supabase-test", as_of=date(2026, 9, 18))


@pytest.fixture
def sample_report():
    sample = FixtureProviders()
    sample.actual.tasks[0].actual_start = date(2026, 9, 2)
    sample.planning.context = {
        "project_name": "Offline persistence contract",
        "analysis": {"deadline_met": True, "utilization_percentage": 72.5},
        "responsible_ai": {"human_review_required": True}, "warnings": [],
    }
    payload = sample.risk.model_dump(mode="json")
    payload["risks"][0].update(
        risk="Vendor integration", confidence=0.92, evidence=["Contract review pending"],
        source="review.pdf#page=3", affected_area="Backend Development",
        recommended_action="Confirm the vendor contract", impact_days=3,
    )
    payload.update(
        summary="One high integration risk",
        evidence_used=[{"document_id": "review-1", "metadata": {"page": 3, "verified": True}},
                       "Contract review pending"],
        knowledge_sources=[{"source": "review.pdf", "pages": [3, 4]}, "Risk register"],
    )
    sample.risk = RiskSnapshot.model_validate(payload)
    return asyncio.run(MonitoringAgent(sample, sample, sample).generate(monitoring_request(), PRINCIPAL))


def stored_row(report):
    payload = report.model_dump(mode="json")
    return {
        "report_id": report.report_id, "project_id": report.project_id,
        "request_id": report.request_id, "generated_at": payload["generated_at"],
        "report": payload,
    }


@pytest.fixture
def repository_factory():
    @asynccontextmanager
    async def factory(handler, *, key=FAKE_KEY, **client_options):
        settings = SupabaseMonitoringSettings(url=BASE_URL, secret_key=SecretStr(key), timeout_seconds=7.5)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_options) as client:
            yield SupabaseMonitoringReportRepository(client, settings)

    return factory


async def operate(repository, operation, report, principal=PRINCIPAL):
    if operation == "save":
        return await repository.save(report, principal)
    if operation == "get":
        return await repository.get(report.project_id, report.report_id, principal)
    if operation == "list":
        return await repository.list(report.project_id, principal)
    if operation == "check_connection":
        return await repository.check_connection()
    raise AssertionError(f"Unknown test operation: {operation}")


def assert_sanitized(error, *sensitive_values):
    displayed = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for value in sensitive_values:
        assert value not in str(error)
        assert value not in repr(error)
        assert value not in displayed
    assert error.__cause__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["mock.legacy.service_role_jwt", FAKE_KEY])
async def test_exact_insert_row_headers_and_no_upsert(repository_factory, sample_report, key):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201)

    async with repository_factory(handler, key=key) as repository:
        assert await repository.save(sample_report, PRINCIPAL) is None
        assert not repository.client.is_closed

    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == BASE_URL + "/rest/v1/monitoring_reports"
    assert sent.headers["apikey"] == key
    if key.startswith("sb_secret_"):
        assert "authorization" not in sent.headers
    else:
        assert sent.headers["authorization"] == f"Bearer {key}"
    assert sent.headers["prefer"] == "return=minimal"
    assert sent.headers["accept-profile"] == sent.headers["content-profile"] == "public"
    assert sent.headers["content-type"] == "application/json"
    assert "on_conflict" not in sent.url.params
    row = json.loads(sent.content)
    assert row == stored_row(sample_report)
    assert row["report"]["risks"][0]["confidence"] == 0.92
    assert row["report"]["risks"][0]["source"] == "review.pdf#page=3"
    assert row["report"]["risk_summary"] == "One high integration risk"
    assert row["report"]["risk_evidence_used"][0]["metadata"] == {"page": 3, "verified": True}
    assert row["report"]["risk_knowledge_sources"][0] == {"source": "review.pdf", "pages": [3, 4]}
    assert row["report"]["risks"][0]["impact_days"] == 3
    assert row["report"]["tasks"][0]["start_delay_days"] == 1
    assert row["report"]["tasks"][0]["planned_deadline_passed"] is False
    assert row["report"]["tasks"][0]["actual_is_stale"] is False
    assert row["report"]["tasks"][0]["last_observed_progress"] is None
    assert row["report"]["metrics"]["stale_actual_tasks"] == 0
    assert row["report"]["planning_context"]["analysis"]["utilization_percentage"] == 72.5
    assert row["report"]["planning_feedback"][0]["action"] == "SCHEDULE_DELAY_DETECTED"
    assert row["report"]["planning_feedback"][0]["data"]["delay_days"] == 1
    assert sent.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 7.5)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "get", "list", "check_connection"])
async def test_requests_never_follow_redirects(repository_factory, sample_report, operation):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "example.supabase.co"
        return httpx.Response(307, headers={"Location": "https://untrusted.example.test/key-leak"})

    async with repository_factory(handler, follow_redirects=True) as repository:
        with pytest.raises(SupabaseMonitoringError, match="HTTP 307"):
            await operate(repository, operation, sample_report)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_validated_round_trip_and_absent_report(repository_factory, sample_report):
    rows = []

    def handler(request):
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        report_filter = request.url.params.get("report_id")
        result = [row for row in rows if report_filter in (None, f"eq.{row['report_id']}")]
        return httpx.Response(200, json=result)

    async with repository_factory(handler) as repository:
        await repository.save(sample_report, PRINCIPAL)
        fetched = await repository.get("P001", sample_report.report_id, PRINCIPAL)
        listed = await repository.list("P001", PRINCIPAL)
        assert isinstance(fetched, MonitoringReport)
        assert fetched == sample_report
        assert fetched is not sample_report
        assert fetched.generated_at == sample_report.generated_at
        assert fetched.risks == sample_report.risks
        assert fetched.risk_evidence_used == sample_report.risk_evidence_used
        assert fetched.risk_knowledge_sources == sample_report.risk_knowledge_sources
        assert fetched.risks[0].impact_days == 3
        assert fetched.tasks[0].start_delay_days == 1
        assert fetched.planning_context == sample_report.planning_context
        assert fetched.planning_feedback == sample_report.planning_feedback
        assert listed == [sample_report]
        assert await repository.get("P001", "absent-report", PRINCIPAL) is None
        rows.clear()
        assert await repository.list("P001", PRINCIPAL) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 1, 100])
async def test_project_scoped_get_and_bounded_list_queries(repository_factory, sample_report, limit):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    async with repository_factory(handler) as repository:
        await repository.get("P001", sample_report.report_id, PRINCIPAL)
        if limit is None:
            await repository.list("P001", PRINCIPAL)
        else:
            await repository.list("P001", PRINCIPAL, limit)

    assert [request.method for request in requests] == ["GET", "GET"]
    assert dict(requests[0].url.params) == {
        "select": SELECT, "project_id": "eq.P001",
        "report_id": f"eq.{sample_report.report_id}", "limit": "1",
    }
    assert dict(requests[1].url.params) == {
        "select": SELECT, "project_id": "eq.P001",
        "order": "generated_at.desc,report_id.desc", "limit": str(20 if limit is None else limit),
    }
    for request in requests:
        assert request.headers["apikey"] == FAKE_KEY
        assert "authorization" not in request.headers
        assert not request.content


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "20", None])
async def test_invalid_list_limits_are_rejected_before_http(repository_factory, limit):
    def handler(request):
        pytest.fail("Invalid limits must not reach HTTP")

    async with repository_factory(handler) as repository:
        with pytest.raises(ValueError, match="integer between 1 and 100"):
            await repository.list("P001", PRINCIPAL, limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["save", "get", "list"])
@pytest.mark.parametrize("denial", ["project", "permission"])
async def test_unauthorized_operations_never_reach_http(repository_factory, sample_report, operation, denial):
    principal = MonitoringPrincipal(
        subject=PRINCIPAL.subject,
        project_ids=frozenset({"P002"}) if denial == "project" else PRINCIPAL.project_ids,
        permissions=PRINCIPAL.permissions if denial == "project" else frozenset(
            {"monitoring:read"} if operation == "save" else {"monitoring:generate"}),
    )

    def handler(request):
        pytest.fail("Authorization must precede HTTP")

    async with repository_factory(handler) as repository:
        with pytest.raises(MonitoringAccessDenied):
            await operate(repository, operation, sample_report, principal)


@pytest.mark.asyncio
async def test_wrong_report_author_is_rejected_before_http(repository_factory, sample_report):
    def handler(request):
        pytest.fail("An author mismatch must not reach HTTP")

    wrong_author = sample_report.model_copy(update={"requested_by": "another-manager"})
    async with repository_factory(handler) as repository:
        with pytest.raises(PermissionError, match="author must match"):
            await repository.save(wrong_author, PRINCIPAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,value", [
    (("report",), None),
    (("unexpected",), "untrusted-stored-value"),
    (("report", "status"), "INVALID"),
    (("report", "risks", 0, "confidence"), 2),
    (("project_id",), "P002"),
    (("report", "project_id"), "P002"),
    (("report_id",), "another-report"),
    (("request_id",), "another-request"),
    (("generated_at",), "2000-01-01T00:00:00Z"),
])
async def test_invalid_or_mismatched_stored_rows_rejected(repository_factory, sample_report, path, value):
    row = deepcopy(stored_row(sample_report))
    target = row
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    async with repository_factory(lambda request: httpx.Response(200, json=[row])) as repository:
        for operation in ("get", "list"):
            with pytest.raises(SupabaseMonitoringError, match="invalid stored|metadata does not match") as error:
                await operate(repository, operation, sample_report)
            assert_sanitized(error.value, "untrusted-stored-value", FAKE_KEY)


@pytest.mark.asyncio
async def test_consistent_cross_project_and_wrong_requested_report_rejected(repository_factory, sample_report):
    row = stored_row(sample_report)
    row["project_id"] = row["report"]["project_id"] = "P002"
    async with repository_factory(lambda request: httpx.Response(200, json=[row])) as repository:
        for operation in ("get", "list"):
            with pytest.raises(SupabaseMonitoringError, match="metadata does not match"):
                await operate(repository, operation, sample_report)
        row = stored_row(sample_report)
        row["report_id"] = row["report"]["report_id"] = "another-report"
        with pytest.raises(SupabaseMonitoringError, match="metadata does not match"):
            await repository.get("P001", sample_report.report_id, PRINCIPAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "list"])
async def test_excess_rows_are_rejected(repository_factory, sample_report, operation):
    rows = [stored_row(sample_report), stored_row(sample_report)]
    async with repository_factory(lambda request: httpx.Response(200, json=rows)) as repository:
        with pytest.raises(SupabaseMonitoringError, match="multiple rows|more reports than requested"):
            if operation == "get":
                await repository.get("P001", sample_report.report_id, PRINCIPAL)
            else:
                await repository.list("P001", PRINCIPAL, limit=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 409, 500])
async def test_http_errors_are_sanitized(repository_factory, sample_report, status):
    body = f"private-database-response: credential={FAKE_KEY}"
    async with repository_factory(lambda request: httpx.Response(status, text=body)) as repository:
        for operation in ("save", "get", "list"):
            with pytest.raises(SupabaseMonitoringError, match=f"HTTP {status}") as error:
                await operate(repository, operation, sample_report)
            assert_sanitized(error.value, body, "private-database-response", FAKE_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_network_and_timeout_errors_are_sanitized(repository_factory, sample_report, exception):
    message = f"private-transport-details: {FAKE_KEY}"

    def handler(request):
        raise exception(message, request=request)

    async with repository_factory(handler) as repository:
        for operation in ("save", "get", "list"):
            with pytest.raises(SupabaseMonitoringError, match="request failed or timed out") as error:
                await operate(repository, operation, sample_report)
            assert_sanitized(error.value, message, "private-transport-details", FAKE_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not JSON", b'{"report":', b"{}", b"null"])
async def test_malformed_or_non_list_json_rejected(repository_factory, sample_report, content):
    async with repository_factory(lambda request: httpx.Response(200, content=content)) as repository:
        for operation in ("get", "list", "check_connection"):
            with pytest.raises(SupabaseMonitoringError, match="invalid report JSON") as error:
                await operate(repository, operation, sample_report)
            assert_sanitized(error.value, FAKE_KEY)


@pytest.fixture
def fake_environment(monkeypatch):
    # Replace the mapping instead of reading or copying the user's real credentials.
    environment = {}
    monkeypatch.setattr(storage.os, "environ", environment)
    return environment


def test_settings_explicit_temp_env_and_process_precedence(tmp_path, fake_environment):
    env_file = tmp_path / "isolated.env"
    env_file.write_text(
        f"SUPABASE_URL={BASE_URL}/\nSUPABASE_SECRET_KEY={FAKE_KEY}\n"
        "SUPABASE_MONITORING_REPORTS_TABLE=file_reports\n", encoding="utf-8",
    )
    settings = SupabaseMonitoringSettings.from_env(env_file)
    assert settings.url == BASE_URL
    assert settings.secret_key.get_secret_value() == FAKE_KEY
    assert settings.reports_table == "file_reports"
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert "**********" in repr(settings.secret_key)

    fake_environment.update(
        SUPABASE_URL="https://override.example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_mock_process_override",
        SUPABASE_MONITORING_REPORTS_TABLE="process_reports",
    )
    overridden = SupabaseMonitoringSettings.from_env(env_file)
    assert overridden.url == "https://override.example.supabase.co"
    assert overridden.secret_key.get_secret_value() == "sb_secret_mock_process_override"
    assert overridden.reports_table == "process_reports"


def test_settings_without_explicit_env_file_never_loads_dotenv(monkeypatch, fake_environment):
    def forbidden_dotenv(*args, **kwargs):
        pytest.fail("An implicit .env must never be accessed")

    monkeypatch.setattr(storage, "dotenv_values", forbidden_dotenv)
    fake_environment.update(SUPABASE_URL=BASE_URL, SUPABASE_SECRET_KEY=FAKE_KEY)
    assert SupabaseMonitoringSettings.from_env().url == BASE_URL


@pytest.mark.parametrize("field,value", [
    ("SUPABASE_URL", "http://private-env-input.example.test"),
    ("SUPABASE_MONITORING_REPORTS_TABLE", "private-env-input.table"),
    ("SUPABASE_MONITORING_OBSERVATIONS_TABLE", "private-env-input.table"),
    ("SUPABASE_SECRET_KEY", "   "),
])
def test_invalid_env_errors_are_generic_without_secrets(tmp_path, fake_environment, field, value):
    values = {"SUPABASE_URL": BASE_URL, "SUPABASE_SECRET_KEY": FAKE_KEY,
              "SUPABASE_MONITORING_REPORTS_TABLE": "monitoring_reports"}
    values[field] = value
    env_file = tmp_path / "isolated.env"
    env_file.write_text("\n".join(f"{name}={item}" for name, item in values.items()), encoding="utf-8")
    with pytest.raises(SupabaseMonitoringConfigurationError) as error:
        SupabaseMonitoringSettings.from_env(env_file)
    assert str(error.value) == (
        "Configure a valid SUPABASE_URL, nonempty SUPABASE_SECRET_KEY, "
        "SUPABASE_MONITORING_REPORTS_TABLE and SUPABASE_MONITORING_OBSERVATIONS_TABLE "
        "in the backend environment"
    )
    assert_sanitized(error.value, FAKE_KEY, "private-env-input")


@pytest.mark.parametrize("url", [
    "http://example.supabase.co", "https:///", "https://user:mock-password@example.supabase.co",
    BASE_URL + "/rest/v1", BASE_URL + "?token=mock", BASE_URL + "#fragment",
])
def test_settings_reject_non_https_or_non_root_urls(url):
    with pytest.raises(ValueError):
        SupabaseMonitoringSettings(url=url, secret_key=SecretStr(FAKE_KEY))


@pytest.mark.parametrize("table", ["public.reports", "reports/other", "reports;DROP", "1reports", "a" * 64, ""])
def test_settings_reject_unsafe_table_names(table):
    with pytest.raises(ValueError):
        SupabaseMonitoringSettings(url=BASE_URL, secret_key=SecretStr(FAKE_KEY), reports_table=table)


def test_settings_accept_https_root_and_simple_custom_table():
    settings = SupabaseMonitoringSettings(url=BASE_URL + "/", secret_key=SecretStr(FAKE_KEY), reports_table="_Reports_2")
    assert settings.url == BASE_URL
    assert settings.reports_table == "_Reports_2"


@pytest.mark.asyncio
async def test_check_connection_is_read_only(repository_factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[{"report_id": "existing-report"}])

    async with repository_factory(handler) as repository:
        assert await repository.check_connection() is None
    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "GET"
    assert dict(sent.url.params) == {"select": "report_id", "limit": "1"}
    assert not sent.content
    assert "prefer" not in sent.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_agent_saves_before_publish_and_failure_prevents_publish(repository_factory, save_fails):
    events = []
    inserted = []

    def handler(request):
        assert request.method == "POST"
        events.append("save")
        inserted.append(json.loads(request.content))
        return httpx.Response(500 if save_fails else 201)

    async def publish(message):
        assert events == ["save"]
        assert inserted == [stored_row(message.report)]
        assert message.correlation_id == "supabase-test"
        assert message.project_id == "P001"
        events.append("publish")

    coordinator = AsyncMock()
    coordinator.publish.side_effect = publish
    sample = FixtureProviders()
    async with repository_factory(handler) as repository:
        agent = MonitoringAgent(sample, sample, sample, repository=repository, coordinator=coordinator)
        if save_fails:
            with pytest.raises(SupabaseMonitoringError, match="HTTP 500"):
                await agent.generate(monitoring_request(), PRINCIPAL)
            coordinator.publish.assert_not_awaited()
            assert events == ["save"]
        else:
            report = await agent.generate(monitoring_request(), PRINCIPAL)
            coordinator.publish.assert_awaited_once()
            assert coordinator.publish.call_args.args[0].report == report
            assert events == ["save", "publish"]


@pytest.mark.asyncio
async def test_agent_authorized_get_and_list(repository_factory, sample_report):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=[stored_row(sample_report)])

    sample = FixtureProviders()
    async with repository_factory(handler) as repository:
        agent = MonitoringAgent(sample, sample, repository=repository)
        assert await agent.get_report("P001", sample_report.report_id, PRINCIPAL) == sample_report
        assert await agent.list_reports("P001", PRINCIPAL, limit=7) == [sample_report]
    assert len(requests) == 2
    assert requests[0].url.params["report_id"] == f"eq.{sample_report.report_id}"
    assert requests[1].url.params["limit"] == "7"
    assert all(request.url.params["project_id"] == "eq.P001" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["project", "permission"])
async def test_agent_read_authorization_precedes_repository_calls(denial):
    principal = MonitoringPrincipal(
        subject="visitor", project_ids=frozenset({"P002"} if denial == "project" else {"P001"}),
        permissions=PRINCIPAL.permissions if denial == "project" else frozenset({"monitoring:generate"}),
    )
    repository = AsyncMock()
    sample = FixtureProviders()
    agent = MonitoringAgent(sample, sample, repository=repository)
    with pytest.raises(MonitoringAccessDenied):
        await agent.get_report("P001", "report-id", principal)
    with pytest.raises(MonitoringAccessDenied):
        await agent.list_reports("P001", principal)
    repository.get.assert_not_awaited()
    repository.list.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_without_repository_still_generates_and_publishes():
    sample = FixtureProviders()
    coordinator = AsyncMock()
    agent = MonitoringAgent(sample, sample, sample, coordinator=coordinator)
    report = await agent.generate(monitoring_request(), PRINCIPAL)
    assert report.project_id == "P001"
    assert report.requested_by == PRINCIPAL.subject
    assert report.metrics.high_risks == 1
    coordinator.publish.assert_awaited_once()
    assert coordinator.publish.call_args.args[0].report == report
    for operation in ("get", "list"):
        with pytest.raises(RuntimeError, match="repository is not configured"):
            if operation == "get":
                await agent.get_report("P001", report.report_id, PRINCIPAL)
            else:
                await agent.list_reports("P001", PRINCIPAL)


@pytest.mark.asyncio
async def test_stale_accuracy_fields_survive_storage_round_trip(repository_factory):
    sample = FixtureProviders()
    sample.actual.tasks[0].updated_on = date(2026, 9, 10)
    report = await MonitoringAgent(sample, sample).generate(monitoring_request(), PRINCIPAL)
    rows = []

    def handler(request):
        if request.method == "POST":
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        assert request.method == "GET"
        return httpx.Response(200, json=rows)

    async with repository_factory(handler) as repository:
        await repository.save(report, PRINCIPAL)
        fetched = await repository.get("P001", report.report_id, PRINCIPAL)
    assert fetched is not None
    assert fetched == report
    assert fetched.status == "AT_RISK"
    assert fetched.metrics.stale_actual_tasks == 1
    assert fetched.metrics.missing_actual_tasks == fetched.metrics.overdue_tasks == fetched.metrics.delayed_tasks == 0
    assert fetched.tasks[0].status == "stale"
    assert fetched.tasks[0].actual_is_stale
    assert fetched.tasks[0].last_observed_progress == 50
    assert fetched.tasks[0].actual_progress is fetched.tasks[0].variance is None
    assert fetched.metrics.actual_progress is fetched.metrics.progress_variance is None
    assert rows[0]["report"]["tasks"][0]["last_observed_progress"] == 50


@pytest.fixture
def observation_receipt():
    return ObservationReceipt(
        observation_id="observation-1", request_id="observation-request", project_id="P001",
        source="field-review", observed_on=date(2026, 9, 18),
        accepted_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc), submitted_by=PRINCIPAL.subject,
        actual_progress={"project_id": "P001", "updated_on": "2026-09-17", "tasks": [{
            "task_id": "T001", "status": "IN_PROGRESS", "progress_percentage": 40,
            "updated_on": "2026-09-17", "actual_start": "2026-09-02",
        }]}, persisted=True, limitations=["Source is caller-claimed"],
    )


def observation_row(receipt):
    payload = receipt.model_dump(mode="json")
    return {**{name: payload[name] for name in (
        "observation_id", "project_id", "request_id", "source", "observed_on", "accepted_at", "submitted_by",
    )}, "receipt": payload}


@pytest.fixture
def observation_repository_factory():
    @asynccontextmanager
    async def factory(handler, *, key=FAKE_KEY):
        settings = SupabaseMonitoringSettings(url=BASE_URL, secret_key=SecretStr(key), timeout_seconds=7.5)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            yield SupabaseMonitoringObservationRepository(client, settings)
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [FAKE_KEY, "mock.legacy.service_role_jwt"])
async def test_observation_exact_insert_roundtrip_and_bound_queries(observation_repository_factory,
                                                                  observation_receipt, key):
    sent, rows = [], []

    def handler(request):
        sent.append(request)
        assert request.url.path == "/rest/v1/monitoring_observations"
        assert request.headers["apikey"] == key
        assert request.headers["accept-profile"] == request.headers["content-profile"] == "public"
        assert "x-api-key" not in request.headers
        if key.startswith("sb_secret_"):
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer " + key
        if request.method == "POST":
            assert request.headers["prefer"] == "return=minimal"
            rows.append(json.loads(request.content))
            return httpx.Response(201)
        assert request.url.params["select"] == SupabaseMonitoringObservationRepository._select
        assert request.url.params["project_id"] == "eq.P001"
        return httpx.Response(200, json=rows)

    async with observation_repository_factory(handler, key=key) as repository:
        await repository.save(observation_receipt, PRINCIPAL)
        assert rows == [observation_row(observation_receipt)]
        assert await repository.get("P001", "observation-1", PRINCIPAL) == observation_receipt
        assert sent[-1].url.params["observation_id"] == "eq.observation-1"
        assert sent[-1].url.params["limit"] == "1"
        assert await repository.list("P001", PRINCIPAL, 1) == [observation_receipt]
        assert sent[-1].url.params["order"] == "accepted_at.desc,observation_id.desc"
        assert sent[-1].url.params["limit"] == "1"
        rows.clear()
        assert await repository.get("P001", "missing", PRINCIPAL) is None
        assert await repository.list("P001", PRINCIPAL) == []
    assert all(request.extensions["timeout"]["read"] == 7.5 for request in sent)
    assert key not in json.dumps(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,permissions,projects", [
    ("save", ["monitoring:read"], ["P001"]),
    ("get", ["monitoring:generate"], ["P001"]),
    ("list", ["monitoring:generate"], ["P001"]),
    ("save", ["monitoring:generate"], ["P002"]),
    ("get", ["monitoring:read"], ["P002"]),
    ("list", ["monitoring:read"], ["P002"]),
])
async def test_observation_repository_authorizes_before_http(observation_repository_factory,
                                                          observation_receipt, operation, permissions, projects):
    principal = MonitoringPrincipal(subject="limited-user", permissions=frozenset(permissions),
                                    project_ids=frozenset(projects))
    async with observation_repository_factory(lambda request: pytest.fail("Unauthorized HTTP reached")) as repository:
        with pytest.raises(MonitoringAccessDenied):
            if operation == "save":
                await repository.save(observation_receipt, principal)
            elif operation == "get":
                await repository.get("P001", "observation-1", principal)
            else:
                await repository.list("P001", principal)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["author", "unsaved", "actual_project"])
async def test_observation_save_revalidates_author_and_payload_before_http(observation_repository_factory,
                                                                        observation_receipt, mutation):
    if mutation == "author":
        observation_receipt.submitted_by = "attacker"
    elif mutation == "unsaved":
        observation_receipt.persisted = False
    else:
        observation_receipt.actual_progress["project_id"] = "P002"
    async with observation_repository_factory(lambda request: pytest.fail("Invalid save reached HTTP")) as repository:
        with pytest.raises((PermissionError, ValueError)):
            await repository.save(observation_receipt, PRINCIPAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("observation_id", "different-id"), ("project_id", "P002"), ("request_id", "different-request"),
    ("source", "other-source"), ("observed_on", "2026-09-17"),
    ("accepted_at", "2026-09-19T12:00:01Z"), ("submitted_by", "attacker"),
    ("accepted_at", "2026-09-19T13:00:00+01:00"), ("accepted_at", "2026-09-19T12:00:00"),
    ("extra", "unexpected"),
])
@pytest.mark.parametrize("operation", ["get", "list"])
async def test_observation_row_metadata_binding(observation_repository_factory, observation_receipt,
                                               field, value, operation):
    row = observation_row(observation_receipt)
    row[field] = value
    async with observation_repository_factory(lambda request: httpx.Response(200, json=[row])) as repository:
        with pytest.raises(SupabaseMonitoringError):
            if operation == "get":
                await repository.get("P001", "observation-1", PRINCIPAL)
            else:
                await repository.list("P001", PRINCIPAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    "actual_project", "actual_extra", "task_extra", "duplicate", "future", "completion", "unsaved",
    "strict_persisted", "non_utc", "invalid_acceptance", "wrong_requested_id", "consistent_other_project",
])
async def test_corrupt_observation_receipts_rejected(observation_repository_factory, observation_receipt, mutation):
    row = observation_row(observation_receipt)
    receipt = row["receipt"]
    if mutation == "actual_project":
        receipt["actual_progress"]["project_id"] = "P002"
    elif mutation == "actual_extra":
        receipt["actual_progress"]["api_key"] = FAKE_KEY
    elif mutation == "task_extra":
        receipt["actual_progress"]["tasks"][0]["secret"] = FAKE_KEY
    elif mutation == "duplicate":
        receipt["actual_progress"]["tasks"] *= 2
    elif mutation == "future":
        receipt["actual_progress"]["tasks"][0]["updated_on"] = "2026-09-20"
    elif mutation == "completion":
        receipt["actual_progress"]["tasks"][0]["progress_percentage"] = 100
    elif mutation == "unsaved":
        receipt["persisted"] = False
    elif mutation == "strict_persisted":
        receipt["persisted"] = "true"
    elif mutation == "non_utc":
        receipt["accepted_at"] = row["accepted_at"] = "2026-09-19T13:00:00+01:00"
    elif mutation == "invalid_acceptance":
        receipt["accepted_at"] = row["accepted_at"] = 0
    elif mutation == "wrong_requested_id":
        receipt["observation_id"] = row["observation_id"] = "other-id"
    else:
        receipt["project_id"] = row["project_id"] = receipt["actual_progress"]["project_id"] = "P002"
    async with observation_repository_factory(lambda request: httpx.Response(200, json=[row])) as repository:
        with pytest.raises(SupabaseMonitoringError) as error:
            await repository.get("P001", "observation-1", PRINCIPAL)
        assert_sanitized(error.value, FAKE_KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, -1, 101, 1.0, "20"])
async def test_observation_repository_limits_before_http(observation_repository_factory, limit):
    async with observation_repository_factory(lambda request: pytest.fail("Invalid limit reached HTTP")) as repository:
        with pytest.raises(ValueError):
            await repository.list("P001", PRINCIPAL, limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "list"])
@pytest.mark.parametrize("kind", ["object", "invalid_json", "excess", "duplicate"])
async def test_observation_invalid_row_lists(observation_repository_factory, observation_receipt, operation, kind):
    row = observation_row(observation_receipt)
    response = (httpx.Response(200, text=FAKE_KEY) if kind == "invalid_json" else
                httpx.Response(200, json={} if kind == "object" else [row, row]))
    async with observation_repository_factory(lambda request: response) as repository:
        with pytest.raises(SupabaseMonitoringError):
            if operation == "get":
                await repository.get("P001", "observation-1", PRINCIPAL)
            else:
                await repository.list("P001", PRINCIPAL, 1 if kind == "excess" else 20)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 404, 409, 500])
@pytest.mark.parametrize("operation", ["save", "get", "list"])
async def test_observation_storage_errors_redacted_no_redirects(observation_repository_factory,
                                                               observation_receipt, status, operation):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(status, text=FAKE_KEY, headers={"Location": "https://untrusted.test/" + FAKE_KEY})

    async with observation_repository_factory(handler) as repository:
        with pytest.raises(SupabaseMonitoringError) as error:
            if operation == "save":
                await repository.save(observation_receipt, PRINCIPAL)
            elif operation == "get":
                await repository.get("P001", "observation-1", PRINCIPAL)
            else:
                await repository.list("P001", PRINCIPAL)
        assert_sanitized(error.value, FAKE_KEY, "untrusted.test")
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_observation_network_errors_redacted(observation_repository_factory, observation_receipt, error_type):
    def handler(request):
        raise error_type(FAKE_KEY, request=request)

    async with observation_repository_factory(handler) as repository:
        with pytest.raises(SupabaseMonitoringError) as error:
            await repository.save(observation_receipt, PRINCIPAL)
        assert_sanitized(error.value, FAKE_KEY)


@pytest.mark.parametrize("table", ["public.observations", "observations/other", "observations;DROP", "1obs", "a" * 64, ""])
def test_observation_table_names_are_validated(table):
    with pytest.raises(ValueError):
        SupabaseMonitoringSettings(url=BASE_URL, secret_key=SecretStr(FAKE_KEY), observations_table=table)


def test_observation_default_and_custom_table_configuration(fake_environment):
    fake_environment.update(SUPABASE_URL=BASE_URL, SUPABASE_SECRET_KEY=FAKE_KEY)
    assert SupabaseMonitoringSettings.from_env().observations_table == "monitoring_observations"
    fake_environment["SUPABASE_MONITORING_OBSERVATIONS_TABLE"] = "_Observations_2"
    assert SupabaseMonitoringSettings.from_env().observations_table == "_Observations_2"


@pytest.mark.asyncio
async def test_report_insert_redacts_mutated_metadata_without_mutating_input(repository_factory, sample_report):
    marker = "synthetic-persistence-credential"
    sample_report.planning_context["metadata"] = {
        "accessToken": marker, "description": "Preserved baseline evidence",
        "records": json.dumps([{"clientSecret": marker, "document_id": "review-1"}]),
        "serialized_evidence": '{ "document_id": "review-1", "page": 3 }',
    }
    sample_report.actual_context = {"source_payload": {"Authorization": marker, "progress_percentage": 40}}
    sample_report.risk_evidence_used[0]["metadata"]["APIKey"] = marker
    sample_report.risk_knowledge_sources.append(json.dumps(json.dumps({"Cookie": marker, "source": "review.pdf"})))
    sample_report.planning_feedback[0].data["transport_metadata"] = {"privateKey": marker, "page": 3}
    originals = deepcopy((sample_report.planning_context, sample_report.actual_context,
                          sample_report.risk_evidence_used, sample_report.risk_knowledge_sources,
                          sample_report.planning_feedback[0].data))
    rows = []

    def handler(request):
        rows.append(json.loads(request.content))
        return httpx.Response(201)

    async with repository_factory(handler) as repository:
        await repository.save(sample_report, PRINCIPAL)
    assert len(rows) == 1 and marker not in json.dumps(rows)
    report = rows[0]["report"]
    metadata = report["planning_context"]["metadata"]
    assert metadata["description"] == "Preserved baseline evidence"
    assert json.loads(metadata["records"]) == [{"document_id": "review-1"}]
    assert metadata["serialized_evidence"] == originals[0]["metadata"]["serialized_evidence"]
    assert report["actual_context"]["source_payload"] == {"progress_percentage": 40}
    assert report["risk_evidence_used"][0]["metadata"] == {"page": 3, "verified": True}
    assert json.loads(json.loads(report["risk_knowledge_sources"][-1])) == {"source": "review.pdf"}
    assert report["planning_feedback"][0]["data"]["transport_metadata"] == {"page": 3}
    assert (sample_report.planning_context, sample_report.actual_context,
            sample_report.risk_evidence_used, sample_report.risk_knowledge_sources,
            sample_report.planning_feedback[0].data) == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "list"])
async def test_legacy_supabase_reports_are_redacted_without_mutating_rows(repository_factory,
                                                                        sample_report, operation):
    row = stored_row(sample_report)
    marker = "synthetic-legacy-storage-credential"
    row["report"]["planning_context"]["metadata"] = {"accessToken": marker, "page": 3}
    row["report"]["actual_context"] = {"source_payload": {"password": marker, "project_id": "P001"}}
    row["report"]["risk_evidence_used"][0]["metadata"]["APIKey"] = marker
    row["report"]["planning_feedback"][0]["data"]["transport_metadata"] = {"Cookie": marker, "page": 3}
    original = deepcopy(row)
    async with repository_factory(lambda request: httpx.Response(200, json=[row])) as repository:
        supplied = await operate(repository, operation, sample_report)
    report = supplied if operation == "get" else supplied[0]
    assert marker not in report.model_dump_json()
    assert report.planning_context["metadata"] == {"page": 3}
    assert report.actual_context["source_payload"] == {"project_id": "P001"}
    assert report.risk_evidence_used[0]["metadata"] == {"page": 3, "verified": True}
    assert report.model_dump()["planning_feedback"][0]["data"]["transport_metadata"] == {"page": 3}
    assert row == original


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "list"])
@pytest.mark.parametrize("as_dict", [False, True])
async def test_agent_redacts_legacy_injected_repository_reports(sample_report, operation, as_dict):
    payload = sample_report.model_dump(mode="json")
    marker = "synthetic-injected-repository-credential"
    payload["planning_context"]["metadata"] = {"clientSecret": marker, "page": 3}
    payload["actual_context"] = {"accessToken": marker, "description": "Historical evidence"}
    original = deepcopy(payload)
    if as_dict:
        legacy = payload
    else:
        legacy = sample_report
        legacy.planning_context = payload["planning_context"]
        legacy.actual_context = payload["actual_context"]
    repository = AsyncMock()
    repository.get.return_value = legacy
    repository.list.return_value = [legacy]
    agent = MonitoringAgent(FixtureProviders(), repository=repository)
    supplied = (await agent.get_report("P001", sample_report.report_id, PRINCIPAL) if operation == "get" else
                (await agent.list_reports("P001", PRINCIPAL))[0])
    assert supplied is not None
    assert supplied.planning_context["metadata"] == {"page": 3}
    assert supplied.actual_context == {"description": "Historical evidence"}
    assert marker not in supplied.model_dump_json()
    assert payload == original
    if not as_dict:
        assert legacy.planning_context == original["planning_context"]
        assert legacy.actual_context == original["actual_context"]
    repository.save.assert_not_awaited()
