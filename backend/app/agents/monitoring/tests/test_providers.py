"""Offline contracts for Risk, Planning, and supplied actual progress providers."""

import hashlib
import json
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.agents.monitoring.agent import MonitoringAccessDenied, MonitoringAgent
from app.agents.monitoring.providers import (
    ActualProgressProvider,
    PlanningAgentAPIError,
    PlanningAgentAPIProvider,
    PlanningMessageProvider,
    RiskAgentAPIError,
    RiskAgentAvailabilityError,
    RiskAgentIntegrityError,
    RiskAgentAPIProvider,
    adapt_actual_progress,
)
from app.agents.monitoring.schemas import (
    ActualProjectSnapshot,
    MonitoringPrincipal,
    MonitoringRequest,
    MonitoringSettings,
)

from .conftest import FixtureProviders


TODAY = date(2026, 9, 18)
BASE_URL = "https://risk.example.test"
PRINCIPAL = MonitoringPrincipal(
    subject="manager", project_ids=frozenset({"P001"}),
    permissions=frozenset({"monitoring:generate"}),
)


@pytest.fixture
def api_payload():
    return {
        "project_id": "P001",
        "risks": [{
            "risk": "External API dependency – release readiness",
            "description": "The external API contract may change before release.",
            "risk_level": "HIGH",
            "confidence": 0.92,
            "evidence": ["Vendor contract is not finalized.", "Integration review is pending."],
            "source": "vendor-review.pdf#page=3",
            "affected_area": "Backend Development",
            "recommended_action": "Confirm the vendor contract and schedule an integration review.",
        }],
        "summary": "One high risk affects integration readiness.",
        "evidence_used": [
            {"document_id": "review-1", "score": 0.94,
             "metadata": {"page": 3, "verified": True, "note": None}},
            "Vendor review excerpt",
        ],
        "knowledge_sources": [
            {"source": "vendor-review.pdf", "pages": [3, 4]},
            "Integration risk register",
        ],
    }


def monitoring_request(as_of=TODAY):
    return MonitoringRequest(project_id="P001", request_id="risk-api-test", as_of=as_of)


def on_track_sample():
    sample = FixtureProviders()
    sample.actual = ActualProjectSnapshot.model_validate({
        "project_id": "P001", "updated_on": TODAY,
        "tasks": [{"task_id": "T001", "progress": 80, "status": "in_progress",
                   "updated_on": TODAY}],
    })
    return sample


@pytest.mark.asyncio
@pytest.mark.parametrize("options,information,top_k,timeout", [
    ({}, "", 5, 30),
    ({"project_information": "P001: backend delivery and vendor integration",
      "top_k": 8, "timeout_seconds": 7.5},
     "P001: backend delivery and vendor integration", 8, 7.5),
])
async def test_exact_post_contract_and_full_snapshot(api_payload, options, information, top_k, timeout):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY, **options)
        snapshot = await provider.get_risks("P001")
        assert not client.is_closed

    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == BASE_URL + "/api/risk/analyse"
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(sent.content) == {
        "project_id": "P001",
        "source_agent": "Monitoring Agent",
        "query": "Identify current project risks relevant to project monitoring.",
        "project_information": information,
        "top_k": top_k,
    }
    assert sent.extensions["timeout"] == {
        "connect": timeout, "read": timeout, "write": timeout, "pool": timeout,
    }
    item = api_payload["risks"][0]
    identity = json.dumps(
        [item["risk"], item["description"], item["source"], item["affected_area"]],
        ensure_ascii=False, separators=(",", ":"),
    )
    expected_id = "risk-api-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    assert snapshot.model_dump() == {
        "project_id": "P001", "updated_on": TODAY, "observed_on": TODAY,
        "risks": [{
            "risk_id": expected_id, "risk": item["risk"],
            "description": item["description"], "level": "high", "status": "open",
            "task_ids": [], "confidence": item["confidence"], "evidence": item["evidence"],
            "source": item["source"], "affected_area": item["affected_area"],
            "recommended_action": item["recommended_action"], "impact_days": None,
        }],
        "knowledge": [], "summary": api_payload["summary"],
        "evidence_used": api_payload["evidence_used"],
        "knowledge_sources": api_payload["knowledge_sources"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["low", "medium", "high", "critical"])
@pytest.mark.parametrize("evidence", ["Vendor review excerpt", ["Vendor review excerpt"]])
async def test_level_normalization_and_evidence_forms(api_payload, level, evidence):
    api_payload["risks"][0].update(risk_level=f" {level.upper()} ", evidence=evidence)
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        snapshot = await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")
    assert snapshot.risks[0].level == level
    assert snapshot.risks[0].evidence == evidence


@pytest.mark.asyncio
async def test_generated_ids_are_stable_across_reordering_and_metadata_changes(api_payload):
    second = deepcopy(api_payload["risks"][0])
    second.update(risk="Staff availability", description="Specialist availability is uncertain.")
    api_payload["risks"].append(second)

    def handler(request):
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        first = await provider.get_risks("P001")
        repeated = await provider.get_risks("P001")
        assert repeated == first
        api_payload["risks"].reverse()
        for item in api_payload["risks"]:
            item.update(risk_level="medium", confidence=0.5, evidence="New evidence",
                        recommended_action="Reassess with the project manager.")
        api_payload["summary"] = "Updated summary"
        provider.today = lambda: date(2026, 9, 19)
        refreshed = await provider.get_risks("P001")
        assert not client.is_closed
        assert refreshed.updated_on == date(2026, 9, 19)
        assert {r.risk: r.risk_id for r in refreshed.risks} == {
            r.risk: r.risk_id for r in first.risks
        }
        assert len({r.risk_id for r in first.risks}) == 2
        api_payload["risks"][0]["description"] = "A different risk identity"
        changed = await provider.get_risks("P001")
        assert changed.risks[0].risk_id != refreshed.risks[0].risk_id
        assert changed.risks[1].risk_id == refreshed.risks[1].risk_id


@pytest.mark.asyncio
async def test_empty_risks_are_a_legitimate_snapshot(api_payload):
    api_payload.update(risks=[], summary="No current risks identified.",
                       evidence_used=[], knowledge_sources=[])
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        snapshot = await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")
    assert snapshot.project_id == "P001"
    assert snapshot.updated_on == TODAY
    assert snapshot.risks == []
    assert snapshot.summary == "No current risks identified."
    assert snapshot.evidence_used == snapshot.knowledge_sources == snapshot.knowledge == []


@pytest.mark.asyncio
async def test_mismatched_project_is_rejected(api_payload):
    api_payload["project_id"] = "P002"
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentIntegrityError, match="project_id does not match"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 401, 403, 404, 408, 422, 429, 500, 502, 503])
async def test_http_errors_are_wrapped(status):
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text="API unavailable")),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match=f"HTTP {status}") as error:
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    expected = RiskAgentAvailabilityError if status in (408, 429) or status >= 500 else RiskAgentIntegrityError
    assert type(error.value) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadError,
                                       httpx.ConnectTimeout, httpx.ReadTimeout,
                                       httpx.RemoteProtocolError, httpx.ProxyError])
async def test_network_and_timeout_errors_are_wrapped(exception):
    def handler(request):
        raise exception("Simulated transport failure", request=request)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RiskAgentAvailabilityError, match="request failed or timed out") as error:
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")
    assert isinstance(error.value.__cause__, exception)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.LocalProtocolError, httpx.UnsupportedProtocol])
async def test_local_protocol_failures_are_not_classified_as_risk_unavailability(exception):
    def handler(request):
        raise exception("Synthetic configuration failure", request=request)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RiskAgentIntegrityError):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not JSON", b'{"risks":'])
async def test_malformed_json_is_rejected(content):
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    ) as client:
        with pytest.raises(RiskAgentIntegrityError, match="invalid JSON or an invalid response schema"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], "not an object"])
async def test_non_object_response_is_rejected(payload):
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(payload))),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match="invalid response schema"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,field", [
    ("response", field) for field in
    ("project_id", "risks", "summary", "evidence_used", "knowledge_sources")
] + [
    ("risk", field) for field in
    ("risk", "description", "risk_level", "confidence", "evidence", "source",
     "affected_area", "recommended_action")
])
async def test_missing_required_response_fields_are_rejected(api_payload, scope, field):
    target = api_payload if scope == "response" else api_payload["risks"][0]
    del target[field]
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match="invalid response schema"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,field,value", [
    ("response", "project_id", "invalid project id"),
    ("response", "risks", {}),
    ("response", "risks", [None]),
    ("response", "summary", None),
    ("response", "evidence_used", {}),
    ("response", "knowledge_sources", "not a list"),
    ("risk", "risk", " "),
    ("risk", "description", " "),
    ("risk", "description", "x" * 2001),
    ("risk", "risk_level", "urgent"),
    ("risk", "risk_level", None),
    ("risk", "confidence", -0.01),
    ("risk", "confidence", 1.01),
    ("risk", "confidence", "NaN"),
    ("risk", "confidence", "Infinity"),
    ("risk", "confidence", None),
    ("risk", "evidence", {"text": "not a string or list"}),
    ("risk", "evidence", [None]),
    ("risk", "source", None),
    ("risk", "affected_area", []),
    ("risk", "recommended_action", {}),
])
async def test_malformed_response_fields_are_rejected(api_payload, scope, field, value):
    target = api_payload if scope == "response" else api_payload["risks"][0]
    target[field] = value
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match="invalid response schema"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
async def test_duplicate_generated_ids_are_rejected(api_payload):
    api_payload["risks"].append(deepcopy(api_payload["risks"][0]))
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match="invalid risk snapshot"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
async def test_api_high_risk_alone_changes_on_track_report_to_at_risk(api_payload):
    sample = on_track_sample()
    settings = MonitoringSettings(deadline_approaching_days=1)
    empty_payload = deepcopy(api_payload)
    empty_payload.update(risks=[], summary="No current risks.", evidence_used=[], knowledge_sources=[])
    responses = [empty_payload, api_payload]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        agent = MonitoringAgent(sample, sample, provider, settings=settings)
        baseline = await agent.generate(monitoring_request(), PRINCIPAL)
        report = await agent.generate(monitoring_request(), PRINCIPAL)

    assert len(requests) == 2
    assert baseline.status == "ON_TRACK"
    assert baseline.warnings == []
    assert baseline.metrics.high_risks == 0
    assert baseline.risks == []
    assert baseline.risk_summary == empty_payload["summary"]
    assert baseline.risk_evidence_used == baseline.risk_knowledge_sources == []
    assert "No risk snapshot supplied; risk coverage is unknown" not in baseline.limitations
    assert report.status == "AT_RISK"
    assert report.metrics.high_risks == 1
    assert report.metrics.model_dump(exclude={"high_risks"}) == baseline.metrics.model_dump(
        exclude={"high_risks"})
    assert report.metrics.actual_progress == report.metrics.expected_progress == 80
    assert report.metrics.progress_variance == report.metrics.delayed_tasks == report.metrics.overdue_tasks == 0
    assert report.tasks == baseline.tasks
    assert report.milestones == baseline.milestones
    assert report.milestones[0].status == "On Track"
    assert report.limitations == baseline.limitations == []
    assert len(report.risks) == 1
    item = api_payload["risks"][0]
    risk = report.risks[0]
    assert risk.model_dump(exclude={"risk_id"}) == {
        "risk": item["risk"], "description": item["description"], "level": "high",
        "status": "open", "task_ids": [], "confidence": item["confidence"],
        "evidence": item["evidence"], "source": item["source"],
        "affected_area": item["affected_area"], "recommended_action": item["recommended_action"],
        "impact_days": None,
    }
    assert report.risk_summary == api_payload["summary"]
    assert report.risk_evidence_used == api_payload["evidence_used"]
    assert report.risk_knowledge_sources == api_payload["knowledge_sources"]
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.code == "HIGH_RISK"
    assert warning.severity == "warning"
    assert warning.message == f"Open high risk {risk.risk_id}: {item['description']}"
    assert warning.source_refs == [f"risk:{risk.risk_id}", item["source"]]
    assert warning.task_ids == []
    assert f"Risk Agent suggestion ({risk.risk_id}): {item['recommended_action']}" in report.suggested_actions
    assert report.summary_method == "deterministic_extractive"
    assert "1 open high/critical risk(s) were supplied." in report.summary
    assert report.human_review_required is True


@pytest.mark.asyncio
async def test_live_api_snapshot_cannot_be_used_for_historical_report(api_payload):
    historical = date(2026, 9, 17)
    sample = on_track_sample()
    sample.planning.updated_on = historical
    sample.actual.updated_on = historical
    sample.actual.tasks[0].updated_on = historical
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        agent = MonitoringAgent(sample, sample, provider)
        with pytest.raises(ValueError, match="Risk snapshot must match the project and not postdate as_of"):
            await agent.generate(monitoring_request(historical), PRINCIPAL)
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("project_ids,permissions", [
    ({"P002"}, {"monitoring:generate"}),
    ({"P001"}, {"monitoring:read"}),
    ({"P001"}, set()),
])
async def test_unauthorized_agent_never_makes_http_requests(api_payload, project_ids, permissions):
    class UnreachableProviders(FixtureProviders):
        async def get_plan(self, project_id):
            pytest.fail("Authorization must precede planning retrieval")

        async def get_actual(self, project_id):
            pytest.fail("Authorization must precede actual retrieval")

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=api_payload)

    principal = MonitoringPrincipal(subject="visitor", project_ids=frozenset(project_ids),
                                    permissions=frozenset(permissions))
    sample = UnreachableProviders()
    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        with pytest.raises(MonitoringAccessDenied):
            await MonitoringAgent(sample, sample, provider).generate(monitoring_request(), principal)
    assert requests == []


OBSERVED_ON = date(2026, 10, 18)
PLANNING_BASE_URL = "https://planning.example.test"


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [30, 7.5])
async def test_planning_exact_get_contract_and_truthful_snapshot(planning_payload, timeout):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=planning_payload)

    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL, headers={"X-API-Key": "offline-planning-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = PlanningAgentAPIProvider(client, timeout_seconds=timeout, today=lambda: OBSERVED_ON)
        snapshot = await provider.get_plan("P001")
        assert not client.is_closed

    assert len(requests) == 1
    sent = requests[0]
    assert sent.method == "GET"
    assert str(sent.url) == PLANNING_BASE_URL + "/api/planning/schedule/P001"
    assert sent.headers["x-api-key"] == "offline-planning-key"
    assert not sent.content
    assert sent.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), timeout)
    assert snapshot.model_dump(exclude={"limitations"}) == {
        "project_id": "P001", "updated_on": None, "fetched_on": OBSERVED_ON,
        "tasks": [{
            "task_id": "T001", "title": "Backend Development",
            "start_date": date(2026, 10, 17), "due_date": date(2026, 10, 27),
            "weight": 1.0, "priority": "high", "is_critical": True, "expected_progress": None,
            "dependency_ids": [],
        }],
        "milestones": [], "context": planning_payload,
    }


@pytest.mark.asyncio
async def test_planning_preserves_authentic_update_date_and_dependencies(planning_payload):
    planning_payload["updated_on"] = "2026-10-17"
    second = deepcopy(planning_payload["schedule"][0])
    second.update(task_id="T002", name="Integration", dependencies=["T001"],
                  planned_start="2026-10-28", planned_end="2026-10-30", priority="MEDIUM")
    planning_payload["schedule"].append(second)
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=planning_payload)),
    ) as client:
        snapshot = await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert snapshot.updated_on == date(2026, 10, 17)
    assert snapshot.fetched_on == OBSERVED_ON
    assert snapshot.tasks[1].dependency_ids == ["T001"]
    assert snapshot.tasks[1].priority == "medium"
    assert snapshot.context == planning_payload
    assert snapshot.milestones == []


@pytest.mark.asyncio
async def test_planning_project_mismatch_has_no_fixture_fallback(planning_payload):
    planning_payload["project_id"] = "P002"
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=planning_payload)),
    ) as client:
        with pytest.raises(ValueError, match="project"):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 404, 500])
async def test_planning_http_errors_do_not_substitute_sample_data(status):
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    ) as client:
        with pytest.raises(ValueError, match=f"HTTP {status}") as error:
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ConnectError, httpx.ReadTimeout])
async def test_planning_transport_errors_are_wrapped(exception):
    def handler(request):
        raise exception("Synthetic offline transport failure", request=request)

    async with httpx.AsyncClient(base_url=PLANNING_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="request failed or timed out") as error:
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert isinstance(error.value.__cause__, exception)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not JSON", b"null", b"[]", b"{}"])
async def test_planning_invalid_responses_have_no_mock_fallback(content):
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    ) as client:
        with pytest.raises(ValueError):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("planned_start", "not-a-date"), ("planned_end", "2026-10-16"),
    ("dependencies", ["UNKNOWN"]), ("dependencies", ["T001"]),
    ("priority", "urgent"), ("name", " "),
])
async def test_planning_invalid_task_contract_is_rejected(planning_payload, field, value):
    planning_payload["schedule"][0][field] = value
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=planning_payload)),
    ) as client:
        with pytest.raises(ValueError):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("include_status", [False, True])
async def test_schedule_created_message_maps_real_schedule(planning_message, include_status):
    if not include_status:
        del planning_message["status"]
    snapshot = await PlanningMessageProvider(planning_message, received_on=OBSERVED_ON).get_plan("P001")
    assert snapshot.project_id == "P001"
    assert snapshot.updated_on is None
    assert snapshot.fetched_on == OBSERVED_ON
    assert snapshot.tasks[0].task_id == "T001"
    assert snapshot.tasks[0].title == "Backend Development"
    assert snapshot.tasks[0].start_date == date(2026, 10, 17)
    assert snapshot.tasks[0].due_date == date(2026, 10, 27)
    assert snapshot.tasks[0].expected_progress is None
    assert snapshot.milestones == []
    for field in ("project_name", "schedule", "analysis", "responsible_ai", "warnings"):
        assert snapshot.context[field] == planning_message["data"][field]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("sender", "risk_agent"), ("receiver", "another_agent"),
    ("action", "CREATE_SCHEDULE"), ("status", "failed"),
    ("status", "pending"), ("status", None), ("data", {}),
])
async def test_invalid_planning_messages_are_rejected(planning_message, field, value):
    planning_message[field] = value
    with pytest.raises(ValueError):
        await PlanningMessageProvider(planning_message, received_on=OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["sender", "receiver", "action", "request_id", "data"])
async def test_incomplete_planning_envelope_is_rejected(planning_message, field):
    del planning_message[field]
    with pytest.raises(ValueError):
        await PlanningMessageProvider(planning_message, received_on=OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
async def test_planning_message_is_project_bound(planning_message):
    with pytest.raises(ValueError, match="project"):
        await PlanningMessageProvider(planning_message, received_on=OBSERVED_ON).get_plan("P002")


@pytest.mark.asyncio
async def test_actual_progress_mapping_preserves_observed_values(actual_payload):
    snapshot = adapt_actual_progress(actual_payload, OBSERVED_ON)
    assert snapshot.model_dump() == {
        "project_id": "P001", "updated_on": OBSERVED_ON, "observed_on": OBSERVED_ON,
        "tasks": [{
            "task_id": "T001", "status": "in_progress", "progress": 40.0,
            "updated_on": OBSERVED_ON, "completed_on": None,
            "actual_start": date(2026, 10, 18),
        }],
        "updates": [],
        "context": {"source_payload": actual_payload, "snapshot_date_basis": "observation",
                    "task_date_basis": {"T001": "observation"}},
    }
    assert await ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON).get_actual("P001") == snapshot


@pytest.mark.asyncio
async def test_actual_progress_preserves_genuine_update_dates(actual_payload):
    actual_payload["updated_on"] = "2026-10-20"
    actual_payload["tasks"][0]["updated_on"] = "2026-10-19"
    snapshot = await ActualProgressProvider(actual_payload, observed_on=date(2026, 10, 21)).get_actual("P001")
    assert snapshot.updated_on == date(2026, 10, 20)
    assert snapshot.observed_on == date(2026, 10, 21)
    assert snapshot.tasks[0].updated_on == date(2026, 10, 19)
    assert snapshot.tasks[0].actual_start == date(2026, 10, 18)


@pytest.mark.parametrize("status", ["NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"])
def test_missing_actual_progress_is_never_invented(actual_payload, status):
    actual_payload["tasks"][0]["status"] = status
    del actual_payload["tasks"][0]["progress_percentage"]
    with pytest.raises(ValueError):
        adapt_actual_progress(actual_payload, OBSERVED_ON)


@pytest.mark.parametrize("actual_end", [None, "2026-10-19"])
def test_completion_requires_authentic_end_not_future_date(actual_payload, actual_end):
    actual_payload["tasks"][0].update(status="COMPLETED", progress_percentage=100, actual_end=actual_end)
    with pytest.raises(ValueError):
        adapt_actual_progress(actual_payload, OBSERVED_ON)


def test_completed_actual_progress_maps_authentic_completion(actual_payload):
    actual_payload["tasks"][0].update(status="COMPLETED", progress_percentage=100, actual_end="2026-10-18")
    snapshot = adapt_actual_progress(actual_payload, OBSERVED_ON)
    assert snapshot.tasks[0].status == "completed"
    assert snapshot.tasks[0].progress == 100
    assert snapshot.tasks[0].completed_on == date(2026, 10, 18)
    assert snapshot.tasks[0].actual_start == date(2026, 10, 18)


@pytest.mark.parametrize("payload", [
    {"tasks": [{"task_id": "T001", "status": "IN_PROGRESS", "progress_percentage": 40}]},
    {"project_id": "P001", "task_id": "T001", "status": "IN_PROGRESS", "progress_percentage": 40},
    {"project_id": "P001", "tasks": {}},
    {"project_id": "P001", "tasks": None},
])
def test_actual_payload_requires_explicit_project_and_tasks_list(payload):
    with pytest.raises(ValueError):
        adapt_actual_progress(payload, OBSERVED_ON)


@pytest.mark.asyncio
async def test_actual_provider_rejects_wrong_requested_project(actual_payload):
    with pytest.raises(ValueError, match="project"):
        await ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON).get_actual("P002")


@pytest.mark.parametrize("field,value", [
    ("status", "UNKNOWN"), ("status", None), ("status", []), ("status", {}),
    ("progress_percentage", -1), ("progress_percentage", 101),
    ("progress_percentage", None), ("actual_start", "2026-10-19"),
])
def test_invalid_actual_observations_are_rejected(actual_payload, field, value):
    actual_payload["tasks"][0][field] = value
    with pytest.raises(ValueError):
        adapt_actual_progress(actual_payload, OBSERVED_ON)


@pytest.mark.asyncio
async def test_risk_api_preserves_source_identity_task_link_and_impact(api_payload):
    api_payload["risks"][0].update(risk_id="source-risk-17", task_id="T001", impact_days=3)
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        first = await provider.get_risks("P001")
        api_payload["risks"][0].update(description="Revised source description", source="new-source.pdf")
        refreshed = await provider.get_risks("P001")
    assert first.risks[0].risk_id == refreshed.risks[0].risk_id == "source-risk-17"
    assert first.risks[0].task_ids == refreshed.risks[0].task_ids == ["T001"]
    assert first.risks[0].impact_days == refreshed.risks[0].impact_days == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [0, -1, 21, True, 1.5, "5", None])
async def test_risk_top_k_is_bounded_before_http(top_k):
    def handler(request):
        pytest.fail("Invalid top_k must not make any HTTP request")

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="top_k"):
            RiskAgentAPIProvider(client, top_k=top_k)


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [1, 20])
async def test_risk_top_k_accepts_both_contract_boundaries(api_payload, top_k):
    def handler(request):
        assert json.loads(request.content)["top_k"] == top_k
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        snapshot = await RiskAgentAPIProvider(client, top_k=top_k, today=lambda: TODAY).get_risks("P001")
    assert snapshot.project_id == "P001"


@pytest.mark.asyncio
async def test_empty_planning_schedule_remains_empty(planning_payload):
    planning_payload["schedule"] = []
    planning_payload["analysis"]["critical_path"] = []
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=planning_payload)),
    ) as client:
        snapshot = await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert snapshot.tasks == snapshot.milestones == []
    assert snapshot.updated_on is None
    assert snapshot.fetched_on == OBSERVED_ON
    assert snapshot.context == planning_payload


def test_actual_start_is_optional_and_defaults_to_none(actual_payload):
    del actual_payload["tasks"][0]["actual_start"]
    snapshot = adapt_actual_progress(actual_payload, OBSERVED_ON)
    assert snapshot.tasks[0].actual_start is None
    assert snapshot.tasks[0].completed_on is None
    assert snapshot.tasks[0].progress == 40


@pytest.mark.asyncio
async def test_duplicate_source_risk_ids_are_rejected(api_payload):
    api_payload["risks"][0]["risk_id"] = "source-risk-17"
    second = deepcopy(api_payload["risks"][0])
    second.update(risk="A different risk", description="A distinct description")
    api_payload["risks"].append(second)
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentAPIError, match="invalid risk snapshot"):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("risk_id", "invalid source id"), ("task_id", "invalid task id"), ("impact_days", -1),
])
async def test_invalid_source_risk_fields_are_rejected(api_payload, field, value):
    api_payload["risks"][0][field] = value
    async with httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=api_payload)),
    ) as client:
        with pytest.raises(RiskAgentAPIError):
            await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")


@pytest.mark.asyncio
@pytest.mark.parametrize("source,error_type", [
    ("planning", PlanningAgentAPIError), ("risk", RiskAgentAPIError),
])
@pytest.mark.parametrize("status", [302, 307])
async def test_api_providers_never_follow_redirects(source, error_type, status):
    base_url = PLANNING_BASE_URL if source == "planning" else BASE_URL
    requests = []

    def handler(sent):
        requests.append(sent)
        assert sent.url.host == httpx.URL(base_url).host
        return httpx.Response(status, headers={"Location": "https://untrusted.example.test/key-leak"})

    async with httpx.AsyncClient(
        base_url=base_url, follow_redirects=True,
        headers={"X-API-Key": "offline-redirect-key", "Authorization": "Bearer offline-token"},
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(error_type, match=f"HTTP {status}") as error:
            if source == "planning":
                await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
            else:
                await RiskAgentAPIProvider(client, today=lambda: TODAY).get_risks("P001")
        assert not client.is_closed
    assert len(requests) == 1
    assert requests[0].headers["x-api-key"] == "offline-redirect-key"
    assert requests[0].headers["authorization"] == "Bearer offline-token"
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tasks", [False, True])
async def test_actual_snapshot_cannot_postdate_explicit_observation(actual_payload, has_tasks):
    actual_payload["updated_on"] = "2026-10-19"
    if not has_tasks:
        actual_payload["tasks"] = []
    with pytest.raises(ValueError, match="Actual snapshot cannot postdate its observation"):
        adapt_actual_progress(actual_payload, OBSERVED_ON)
    provider = ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON)
    with pytest.raises(ValueError, match="Actual snapshot cannot postdate its observation"):
        await provider.get_actual("P001")


def test_absent_actual_snapshot_has_no_invented_dates():
    assert ActualProjectSnapshot(project_id="P001").model_dump() == {
        "project_id": "P001", "updated_on": None, "observed_on": None,
        "tasks": [], "updates": [], "context": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("null_task_date", [False, True])
async def test_undated_task_cannot_inherit_older_project_update(actual_payload, null_task_date):
    actual_payload["updated_on"] = "2026-10-17"
    if null_task_date:
        actual_payload["tasks"][0]["updated_on"] = None
    with pytest.raises(ValueError, match="Task update cannot be newer than its snapshot"):
        adapt_actual_progress(actual_payload, OBSERVED_ON)
    provider = ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON)
    with pytest.raises(ValueError, match="Task update cannot be newer than its snapshot"):
        await provider.get_actual("P001")


def test_genuinely_dated_task_preserves_older_source_dates_and_new_observation(actual_payload):
    actual_payload["updated_on"] = "2026-10-17"
    actual_payload["tasks"][0].update(updated_on="2026-10-17", actual_start="2026-10-17")
    snapshot = adapt_actual_progress(actual_payload, OBSERVED_ON)
    assert snapshot.updated_on == snapshot.tasks[0].updated_on == date(2026, 10, 17)
    assert snapshot.observed_on == OBSERVED_ON
    assert snapshot.tasks[0].actual_start == date(2026, 10, 17)
    assert snapshot.tasks[0].progress == 40


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,path,expected", [
    (None, ["T001"], True), (None, [], False),
    (False, ["T001"], False), (True, [], True),
])
@pytest.mark.parametrize("priority", ["LOW", "MEDIUM"])
async def test_critical_flag_preserves_authority_without_inventing_priority(planning_payload, flag, path, expected, priority):
    task = planning_payload["schedule"][0]
    task["priority"] = priority
    if flag is None:
        del task["is_critical"]
    else:
        task["is_critical"] = flag
    planning_payload["analysis"]["critical_path"] = path
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda sent: httpx.Response(200, json=planning_payload)),
    ) as client:
        snapshot = await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert snapshot.tasks[0].is_critical is expected
    assert snapshot.tasks[0].priority == priority.lower()
    assert snapshot.tasks[0].expected_progress is None
    assert snapshot.context == planning_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("deadline_met", "false"), ("deadline_met", "true"), ("deadline_met", 0),
    ("deadline_met", 1), ("deadline_met", None), ("deadline_met", []),
    ("critical_path", "T001"), ("critical_path", None), ("critical_path", ["UNKNOWN"]),
    ("critical_path", ["T001", "T001"]), ("critical_path", [" T001 "]),
    ("critical_path", [1]), ("critical_path", [{}]),
])
async def test_malformed_planning_analysis_is_rejected_without_rewriting_refs(planning_payload, field, value):
    planning_payload["analysis"][field] = value
    original = deepcopy(planning_payload)
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda sent: httpx.Response(200, json=planning_payload)),
    ) as client:
        with pytest.raises(PlanningAgentAPIError, match="invalid schedule schema"):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")
    assert planning_payload == original


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["true", "false", 0, 1, None])
async def test_malformed_source_critical_flag_is_not_coerced(planning_payload, value):
    planning_payload["schedule"][0]["is_critical"] = value
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda sent: httpx.Response(200, json=planning_payload)),
    ) as client:
        with pytest.raises(PlanningAgentAPIError):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
async def test_empty_schedule_cannot_silently_discard_critical_path_refs(planning_payload):
    planning_payload["schedule"] = []
    async with httpx.AsyncClient(
        base_url=PLANNING_BASE_URL,
        transport=httpx.MockTransport(lambda sent: httpx.Response(200, json=planning_payload)),
    ) as client:
        with pytest.raises(PlanningAgentAPIError):
            await PlanningAgentAPIProvider(client, today=lambda: OBSERVED_ON).get_plan("P001")


@pytest.mark.asyncio
async def test_planning_message_preserves_original_envelope_and_source_metadata(planning_message):
    planning_message["transport_metadata"] = {"correlation_id": "original-correlation"}
    original = deepcopy(planning_message)
    provider = PlanningMessageProvider(planning_message, received_on=OBSERVED_ON)
    planning_message["request_id"] = "later-mutation"
    snapshot = await provider.get_plan("P001")
    assert snapshot.context["message_source"] == {
        "request_id": original["request_id"], "sender": original["sender"],
        "received_on": OBSERVED_ON.isoformat(), "message": original,
    }
    for key, value in original["data"].items():
        assert snapshot.context[key] == value


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", ["", "Existing caller-supplied project context"])
async def test_contextual_risk_method_keeps_endpoint_fields_and_configured_context(api_payload, configured):
    sent_requests = []
    context = {"project_id": "P001", "as_of": TODAY.isoformat(),
               "planned_baseline": {"tasks": []}, "observed_actual_measurements": {"tasks": []}}

    def handler(sent):
        sent_requests.append(sent)
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, project_information=configured, top_k=7, today=lambda: TODAY)
        snapshot = await provider.get_risks_for_monitoring("P001", json.dumps(context))
        assert provider.project_information == configured
    assert snapshot.observed_on == TODAY
    assert len(sent_requests) == 1
    sent = sent_requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == BASE_URL + "/api/risk/analyse"
    body = json.loads(sent.content)
    assert set(body) == {"project_id", "source_agent", "query", "project_information", "top_k"}
    assert body["source_agent"] == "Monitoring Agent"
    assert body["top_k"] == 7
    expected = dict(context)
    if configured:
        expected["configured_project_information"] = configured
    assert json.loads(body["project_information"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["not JSON", "[]", "null", '{"project_id":"P002"}'])
async def test_invalid_contextual_risk_json_is_rejected_before_http(context):
    def unreachable(sent):
        pytest.fail("Invalid context must not reach Risk")

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(unreachable)) as client:
        with pytest.raises(ValueError, match="project-bound JSON object"):
            await RiskAgentAPIProvider(client).get_risks_for_monitoring("P001", context)


@pytest.mark.asyncio
async def test_agent_sends_validated_baseline_and_separately_labeled_measurements(api_payload, planning_message, actual_payload):
    configured = "Retain this trusted project description"
    planning_message["data"]["updated_on"] = "2026-10-17"
    actual_payload["updated_on"] = "2026-10-17"
    actual_payload["tasks"][0].update(updated_on="2026-10-17", actual_start="2026-10-17")
    requests = []

    def handler(sent):
        requests.append(sent)
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(
        base_url=BASE_URL, headers={"Authorization": "Bearer offline-private-token", "X-API-Key": "offline-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        risk = RiskAgentAPIProvider(client, project_information=configured, today=lambda: OBSERVED_ON)
        report = await MonitoringAgent(
            PlanningMessageProvider(planning_message, received_on=OBSERVED_ON),
            ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON), risk,
        ).generate(monitoring_request(OBSERVED_ON), PRINCIPAL)
    information = json.loads(requests[0].content)["project_information"]
    context = json.loads(information)
    assert context["project_id"] == "P001"
    assert context["as_of"] == OBSERVED_ON.isoformat()
    assert context["source_dates"] == {
        "planning_updated_on": "2026-10-17", "planning_observed_on": "2026-10-18",
        "actual_updated_on": "2026-10-17", "actual_observed_on": "2026-10-18",
    }
    assert context["planned_baseline"]["tasks"][0]["is_critical"] is True
    assert "expected_progress" not in context["planned_baseline"]["tasks"][0]
    assert "weight" not in context["planned_baseline"]["tasks"][0]
    assert context["planned_baseline"]["context"]["message_source"]["request_id"] == planning_message["request_id"]
    assert context["observed_actual_measurements"]["tasks"][0]["progress"] == 40
    assert context["configured_project_information"] == configured
    assert "offline-private-token" not in information and "offline-key" not in information
    assert "requested_by" not in context and "principal" not in context
    assert report.coverage is not None
    assert report.coverage.risk_status == "available"
    assert report.coverage.risk_observed_on == OBSERVED_ON


@pytest.mark.asyncio
@pytest.mark.parametrize("has_source_update", [False, True])
async def test_actual_coverage_preserves_source_vs_observation_date_basis(planning_message, actual_payload, has_source_update):
    if has_source_update:
        actual_payload["updated_on"] = "2026-10-17"
        actual_payload["tasks"][0].update(updated_on="2026-10-17", actual_start="2026-10-17")
    original = deepcopy(actual_payload)
    report = await MonitoringAgent(
        PlanningMessageProvider(planning_message, received_on=OBSERVED_ON),
        ActualProgressProvider(actual_payload, observed_on=OBSERVED_ON),
    ).generate(monitoring_request(OBSERVED_ON), PRINCIPAL)
    assert report.coverage is not None
    assert report.coverage.actual_updated_on == (date(2026, 10, 17) if has_source_update else None)
    assert report.coverage.actual_observed_on == OBSERVED_ON
    assert report.actual_context["source_payload"] == original
    assert report.actual_context["snapshot_date_basis"] == ("source_update" if has_source_update else "observation")


@pytest.mark.asyncio
@pytest.mark.parametrize("contextual", [False, True])
async def test_risk_http_boundary_filters_configured_and_serialized_credentials(api_payload, contextual):
    marker = "synthetic-sensitive-context"
    configured = json.dumps({
        "accessToken": marker, "description": "Preserve the vendor review",
        "records": json.dumps([{"clientSecret": marker, "document_id": "review-1"}]),
    })
    context = {
        "project_id": "P001", "transport_metadata": {"APIKey": marker, "page": 3},
        "evidence": json.dumps({"Authorization": marker, "description": "Review pending"}),
    }
    original = deepcopy(context)
    requests = []

    def handler(sent):
        requests.append(sent)
        return httpx.Response(200, json=api_payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, project_information=configured, today=lambda: TODAY)
        if contextual:
            await provider.get_risks_for_monitoring("P001", json.dumps(context))
        else:
            await provider.get_risks("P001")
        assert provider.project_information == configured
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    information = body["project_information"]
    assert marker not in information
    forwarded = json.loads(information)
    if contextual:
        assert forwarded["project_id"] == "P001"
        assert forwarded["transport_metadata"] == {"page": 3}
        assert json.loads(forwarded["evidence"]) == {"description": "Review pending"}
        forwarded = json.loads(forwarded["configured_project_information"])
    assert forwarded["description"] == "Preserve the vendor review"
    assert json.loads(forwarded["records"]) == [{"document_id": "review-1"}]
    assert context == original


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["fail", "report_unknown"])
@pytest.mark.parametrize("failure", ["project", "schema", "risk_field", "duplicates", "json", "non_object", "task"])
async def test_real_risk_adapter_integrity_failures_never_save_or_publish(api_payload, policy, failure):
    sample = FixtureProviders()
    if failure == "project":
        api_payload["project_id"] = "P002"
    elif failure == "schema":
        del api_payload["summary"]
    elif failure == "risk_field":
        api_payload["risks"][0]["confidence"] = 2
    elif failure == "duplicates":
        api_payload["risks"].append(deepcopy(api_payload["risks"][0]))
    elif failure == "task":
        api_payload["risks"][0]["task_id"] = "UNKNOWN"
    requests = []

    def handler(sent):
        requests.append(sent)
        if failure == "json":
            return httpx.Response(200, text="Synthetic invalid JSON")
        return httpx.Response(200, json=[] if failure == "non_object" else api_payload)

    repository = Mock(spec=["save"], save=AsyncMock())
    coordinator = Mock(spec=["publish"], publish=AsyncMock())
    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)) as client:
        provider = RiskAgentAPIProvider(client, today=lambda: TODAY)
        expected = ValueError if failure == "task" else RiskAgentIntegrityError
        with pytest.raises(expected):
            await MonitoringAgent(sample, sample, provider, repository=repository, coordinator=coordinator,
                                  settings=MonitoringSettings(risk_failure_policy=policy)).generate(
                monitoring_request(), PRINCIPAL)
    assert len(requests) == 1
    repository.save.assert_not_awaited()
    coordinator.publish.assert_not_awaited()
