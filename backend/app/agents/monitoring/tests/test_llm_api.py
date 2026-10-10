"""Offline API contracts for direct Groq selection of verified Monitoring facts."""

import json
from contextlib import contextmanager
from dataclasses import fields
from datetime import timedelta
from types import SimpleNamespace

import dotenv
import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agents.monitoring import api
from app.agents.monitoring.agent import MonitoringTokenAuthenticator
from app.agents.monitoring.schemas import MonitoringPrincipal, SummarySelection
from app.agents.monitoring.summarizer import GROQ_BASE_URL

from . import test_api as api_fixtures
from .test_api import (
    DB_KEY,
    HEADERS,
    KEY as PLANNING_KEY,
    PRINCIPAL,
    ROOT,
    TODAY,
    TOKEN,
    MemoryRepository,
    OfflinePlanning,
    OfflineRisk,
    assert_redacted,
    mock_clients,
    request_body,
)


# Expose the existing fixtures for pytest discovery in this module.
auth = api_fixtures.auth
schedule = api_fixtures.schedule
observations = api_fixtures.observations
risk_response = api_fixtures.risk_response

GROQ_KEY = "synthetic-api-groq-key-not-a-real-credential"
MODEL = "openai/gpt-oss-120b"
FALLBACK = "AI fact selection unavailable or invalid; deterministic summary used"
UPSTREAM_DETAIL = "synthetic-private-upstream-detail"
ANALYSIS_FIELDS = (
    "status", "metrics", "tasks", "milestones", "warnings", "coverage", "facts",
    "risks", "risk_summary", "risk_evidence_used", "risk_knowledge_sources",
    "planning_context", "actual_context", "planning_feedback", "suggested_actions",
)


@pytest.fixture(autouse=True)
def isolated_api_environment(monkeypatch):
    # Substitute the module's environment, never inspect operator credential values.
    monkeypatch.setattr(api, "os", SimpleNamespace(environ={}))
    read_synthetic_dotenv = dotenv.dotenv_values

    def read(path, **options):
        assert path.name == "isolated-groq.env", "Real .env files must never be read"
        return read_synthetic_dotenv(path, **options)

    monkeypatch.setattr(api, "dotenv_values", read)


def completion(content='{"fact_ids":["status"]}'):
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": content,
    }}]}


def groq_requests(requests):
    return [request for request in requests if request.url.host == "api.groq.com"]


def assert_no_credentials(response, *extra):
    assert_redacted(response, GROQ_KEY, UPSTREAM_DETAIL, *extra)


def assert_analysis_unchanged(report, baseline):
    for name in ANALYSIS_FIELDS:
        assert report[name] == baseline[name], name


@pytest.fixture
def live_api(auth, schedule):
    @contextmanager
    def build(*, mode="valid", enabled=True, key=GROQ_KEY, repository=None):
        sent = []

        def handle(request):
            sent.append(request)
            if request.url.host == "planning.example.test":
                assert request.method == "GET"
                assert request.url.path == "/api/planning/schedule/P001"
                assert request.headers["x-api-key"] == PLANNING_KEY
                assert "authorization" not in request.headers
                assert GROQ_KEY not in str(request.headers)
                return httpx.Response(200, json=schedule)
            assert str(request.url) == GROQ_BASE_URL + "/openai/v1/chat/completions"
            assert request.method == "POST"
            assert request.headers["Authorization"] == "Bearer " + GROQ_KEY
            assert "x-api-key" not in request.headers
            for secret in (TOKEN, PLANNING_KEY, DB_KEY, GROQ_KEY):
                assert secret not in request.content.decode()
            payload = json.loads(request.content)
            assert payload["model"] == MODEL
            facts = json.loads(payload["messages"][1]["content"])
            assert all(set(fact) == {"fact_id", "text"} for fact in facts)
            assert "source_refs" not in request.content.decode()
            if mode == "timeout":
                raise httpx.ReadTimeout(GROQ_KEY + " " + UPSTREAM_DETAIL, request=request)
            if mode == "http_error":
                return httpx.Response(401, text=GROQ_KEY + " " + UPSTREAM_DETAIL)
            if mode == "malformed":
                return httpx.Response(200, content=b"not JSON")
            if mode == "invalid_selection":
                return httpx.Response(200, json=completion(json.dumps({
                    "fact_ids": ["status", GROQ_KEY + " " + UPSTREAM_DETAIL],
                })))
            assert mode == "valid"
            return httpx.Response(200, json=completion())

        factory, clients = mock_clients(handle)
        settings = api.MonitoringAPISettings(
            planning_base_url="https://planning.example.test",
            risk_base_url="https://risk.example.test",
            planning_api_key=SecretStr(PLANNING_KEY),
            groq_api_key=SecretStr(key) if key is not None else None,
            groq_model=MODEL, llm_enabled=enabled,
        )
        service = api.create_app(settings, authenticator=auth, repository=repository,
                                 client_factory=factory)
        with TestClient(service) as client:
            yield SimpleNamespace(client=client, requests=sent, clients=clients, settings=settings)
        assert all(client.is_closed for client in clients)

    return build


def test_live_groq_report_with_risks_disabled_uses_external_request_owned_clients(live_api):
    with live_api() as service:
        health = service.client.get(ROOT + "/health")
        assert health.status_code == 200
        assert health.json()["capabilities"]["llm_configured"] is True
        assert health.json()["live_sources_verified"] is False
        assert service.requests == service.clients == []
        for _ in range(2):
            response = service.client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
            assert response.status_code == 200
            report = response.json()
            assert report["summary_method"] == "llm_extractive"
            assert report["summary"].startswith("AI-generated fact selection")
            assert FALLBACK not in report["limitations"]
            assert report["coverage"]["risk_status"] == "not_supplied"
            assert report["risks"] == []
            assert_no_credentials(response)
            assert all(client.is_closed for client in service.clients)
        assert len(service.clients) == 4
        assert len({id(client) for client in service.clients}) == 4
        assert [request.url.host for request in service.requests] == [
            "planning.example.test", "api.groq.com", "planning.example.test", "api.groq.com",
        ]
        assert [str(client.base_url).rstrip("/") for client in service.clients].count(GROQ_BASE_URL) == 2


def test_selector_cannot_omit_unknown_coverage_or_change_analysis(live_api):
    with live_api(enabled=False) as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
        assert response.status_code == 200
        baseline = response.json()
    with live_api() as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
        assert response.status_code == 200
        report = response.json()
        assert_analysis_unchanged(report, baseline)
        assert set(report["summary_fact_ids"]) == {"status", "coverage", "risk_coverage", "milestones"}
        facts = {fact["fact_id"]: fact["text"] for fact in report["facts"]}
        for fact_id in report["summary_fact_ids"]:
            assert facts[fact_id] in report["summary"]
        assert facts["tasks"] not in report["summary"]
        assert "Progress cannot be calculated" in report["summary"]
        assert "Current risk coverage is unknown" in report["summary"]
        assert "No milestone definitions were supplied" in report["summary"]
        assert report["status"] == "AT_RISK"
        assert report["metrics"]["actual_progress"] is None
        assert report["metrics"]["missing_actual_tasks"] == 1
        assert report["coverage"]["actual_status"] == "missing"
        assert report["coverage"]["milestones_status"] == "not_supplied"
        assert "MISSING_ACTUAL" in {warning["code"] for warning in report["warnings"]}
        assert report["limitations"] == baseline["limitations"]
        assert_no_credentials(response)


@pytest.mark.parametrize("mode", ["malformed", "invalid_selection", "timeout", "http_error"])
def test_groq_failure_falls_back_and_saves_exact_report(live_api, mode, caplog):
    with live_api(enabled=False) as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
        assert response.status_code == 200
        baseline = response.json()
    repository = MemoryRepository()
    with live_api(mode=mode, repository=repository) as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS,
                                       json=request_body(save_report=True))
        assert response.status_code == 200
        report = response.json()
        assert report["summary_method"] == "deterministic_extractive"
        assert report["summary"] == baseline["summary"]
        assert report["summary_fact_ids"] == baseline["summary_fact_ids"]
        assert report["limitations"] == baseline["limitations"] + [FALLBACK]
        assert report["limitations"].count(FALLBACK) == 1
        assert_analysis_unchanged(report, baseline)
        assert repository.calls == [("save", "P001", PRINCIPAL.subject)]
        assert repository.reports[report["report_id"]].model_dump(mode="json") == report
        assert len(groq_requests(service.requests)) == 1
        assert_no_credentials(response)
        stored = repository.reports[report["report_id"]].model_dump_json()
        for secret in (GROQ_KEY, TOKEN, PLANNING_KEY, DB_KEY, UPSTREAM_DETAIL):
            assert secret not in stored
            assert secret not in caplog.text


@pytest.mark.parametrize("enabled, key, warning", [
    (True, None, True), (False, GROQ_KEY, False), (False, None, False),
])
def test_missing_key_and_disabled_flag_never_dispatch_groq(live_api, enabled, key, warning):
    repository = MemoryRepository()
    with live_api(enabled=enabled, key=key, repository=repository) as service:
        health = service.client.get(ROOT + "/health")
        assert health.status_code == 200
        assert health.json()["capabilities"]["llm_configured"] is False
        assert health.json()["status"] == "ready"
        assert health.json()["live_sources_verified"] is False
        assert service.requests == service.clients == []
        response = service.client.post(ROOT + "/reports", headers=HEADERS,
                                       json=request_body(save_report=True))
        assert response.status_code == 200
        report = response.json()
        assert report["summary_method"] == "deterministic_extractive"
        assert report["limitations"].count(FALLBACK) == int(warning)
        assert groq_requests(service.requests) == []
        assert all(client.base_url.host != "api.groq.com" for client in service.clients)
        assert repository.reports[report["report_id"]].model_dump(mode="json") == report
        assert_no_credentials(response)


class RecordingSummarizer:
    def __init__(self):
        self.calls = []

    async def select_facts(self, facts):
        self.calls.append([fact.model_copy(deep=True) for fact in facts])
        return SummarySelection(fact_ids=["status"])


@pytest.mark.parametrize("mount", ["app", "router"])
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("configured_key", [True, False])
def test_injected_summarizer_is_used_only_when_enabled(auth, schedule, mount, enabled, configured_key):
    selector = RecordingSummarizer()
    planning, risk = OfflinePlanning(schedule), OfflineRisk()
    factory, clients = mock_clients(lambda request: pytest.fail("Injected dependencies must not dispatch HTTP"))
    settings = api.MonitoringAPISettings(
        groq_api_key=SecretStr(GROQ_KEY) if configured_key else None, llm_enabled=enabled,
    )
    if mount == "app":
        service = api.create_app(
            settings, authenticator=auth, planning_provider_factory=lambda client: planning,
            risk_provider_factory=lambda client: risk, summarizer=selector, client_factory=factory,
        )
    else:
        service = FastAPI()
        service.include_router(api.create_router(
            settings, authenticator=auth, planning_provider_factory=lambda client: planning,
            risk_provider_factory=lambda client: risk, summarizer=selector, client_factory=factory,
        ))
    with TestClient(service) as client:
        health = client.get(ROOT + "/health")
        assert health.status_code == 200
        assert health.json()["capabilities"]["llm_configured"] is enabled
        assert selector.calls == clients == planning.calls == risk.calls == []
        response = client.post(ROOT + "/reports", headers=HEADERS, json=request_body())
        assert response.status_code == 200
        report = response.json()
        assert report["summary_method"] == ("llm_extractive" if enabled else "deterministic_extractive")
        assert len(selector.calls) == int(enabled)
        assert FALLBACK not in report["limitations"]
        assert risk.calls == []
        assert_no_credentials(response)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("case, status", [
    ("no_auth", 401), ("bad_auth", 401), ("out_of_scope", 403), ("read_only", 403),
    ("extra_field", 422), ("invalid_bool", 422), ("future", 422),
    ("actual_without_date", 422), ("wrong_actual_project", 422),
    ("unknown_actual_task", 422), ("malformed_json", 422),
])
def test_denied_or_invalid_generation_never_calls_groq(auth, schedule, observations, case, status):
    repository = MemoryRepository()
    planning = OfflinePlanning(schedule)
    sent = []
    factory, clients = mock_clients(
        lambda request: sent.append(request) or pytest.fail("Rejected input must not dispatch HTTP"),
    )
    headers, body = HEADERS, request_body(save_report=True)
    if case == "no_auth":
        headers = {}
    elif case == "bad_auth":
        headers = {"Authorization": "Bearer " + "synthetic-invalid-token-" + "z" * 40}
    elif case == "out_of_scope":
        body["project_id"] = "P999"
    elif case == "read_only":
        reader = MonitoringPrincipal(subject="offline-reader", project_ids=frozenset({"P001"}),
                                     permissions=frozenset({"monitoring:read"}))
        auth = MonitoringTokenAuthenticator({TOKEN: reader})
    elif case == "extra_field":
        body["groq_api_key"] = GROQ_KEY
    elif case == "invalid_bool":
        body["include_risks"] = "false"
    elif case == "future":
        body["as_of"] = (TODAY + timedelta(days=1)).isoformat()
    elif case == "actual_without_date":
        body["actual_progress"] = observations
    elif case in {"wrong_actual_project", "unknown_actual_task"}:
        body.update(actual_progress=observations, observed_on=TODAY.isoformat())
        if case == "wrong_actual_project":
            observations["project_id"] = "P999"
        else:
            observations["tasks"][0]["task_id"] = "UNKNOWN"
    service = api.create_app(
        api.MonitoringAPISettings(groq_api_key=SecretStr(GROQ_KEY)), authenticator=auth,
        planning_provider_factory=lambda client: planning, repository=repository, client_factory=factory,
    )
    with TestClient(service) as client:
        if case == "malformed_json":
            response = client.post(ROOT + "/reports", headers={**headers, "Content-Type": "application/json"},
                                   content='{"groq_api_key":"' + GROQ_KEY + '"')
        else:
            response = client.post(ROOT + "/reports", headers=headers, json=body)
        assert response.status_code == status
        assert repository.calls == sent == []
        assert_no_credentials(response)
    assert all(client.is_closed for client in clients)


def test_history_and_exports_return_saved_report_without_reselecting(live_api, auth):
    repository = MemoryRepository()
    with live_api(repository=repository) as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS,
                                       json=request_body(save_report=True))
        assert response.status_code == 200
        saved = response.json()
        assert saved["summary_method"] == "llm_extractive"
        assert len(groq_requests(service.requests)) == 1
    factory, clients = mock_clients(lambda request: pytest.fail("History must never dispatch Groq or providers"))
    selector = RecordingSummarizer()
    service = api.create_app(
        api.MonitoringAPISettings(groq_api_key=SecretStr(GROQ_KEY)), authenticator=auth,
        repository=repository, summarizer=selector, client_factory=factory,
        planning_provider_factory=lambda client: pytest.fail("History must not load Planning"),
        risk_provider_factory=lambda client: pytest.fail("History must not load Risk"),
    )
    path = ROOT + "/projects/P001/reports"
    with TestClient(service) as client:
        for suffix in ("", "/" + saved["report_id"], "/" + saved["report_id"] + "/coordinator-message"):
            response = client.get(path + suffix, headers=HEADERS)
            assert response.status_code == 200
            payload = response.json()
            report = payload[0] if not suffix else payload["report"] if suffix.endswith("coordinator-message") else payload
            assert report == saved
            assert_no_credentials(response)
    assert clients == selector.calls == []
    assert repository.reports[saved["report_id"]].model_dump(mode="json") == saved


@pytest.fixture
def shared_groq_api(monkeypatch, schedule, risk_response):
    @contextmanager
    def build(*, revoke_after_risk=False):
        service = FastAPI()
        planning = OfflinePlanning(schedule)
        events, requests, rows, clients, options_seen, internal_groq = [], [], [], [], [], []

        class FakeAccountAccess:
            revoked = False
            checks = 0

            async def get_plan(self, project_id):
                events.append("planning")
                return await planning.get_plan(project_id)

            async def recheck(self, project_id):
                assert project_id == "P001"
                self.checks += 1
                events.append(f"recheck:{self.checks}")
                if self.revoked:
                    raise HTTPException(403, "Insufficient Monitoring project access")

        access = FakeAccountAccess()

        async def principal(runtime, request: Request):
            assert runtime.account_mode is True
            assert request.headers["authorization"] == HEADERS["Authorization"]
            events.append("principal")
            request.state.monitoring_access = access
            return PRINCIPAL

        monkeypatch.setattr(api._Runtime, "principal", principal)

        @service.post("/api/risk/analyse")
        async def risk(request: Request):
            events.append("risk")
            assert access.checks == 2
            assert planning.calls == ["P001"]
            assert "authorization" not in request.headers
            assert "x-api-key" not in request.headers
            payload = await request.json()
            assert payload["project_id"] == "P001"
            context = json.loads(payload["project_information"])
            assert context["planned_baseline"]["tasks"][0]["task_id"] == "T001"
            for secret in (TOKEN, GROQ_KEY, PLANNING_KEY, DB_KEY):
                assert secret not in json.dumps(payload)
            access.revoked = revoke_after_risk
            return risk_response

        @service.post("/openai/v1/chat/completions")
        async def wrong_internal_groq():
            internal_groq.append(True)
            pytest.fail("Groq must never use the shared application's ASGI transport")

        def network(request):
            requests.append(request)
            if request.url.host == "api.groq.com":
                events.append("groq")
                assert access.checks == 3
                assert access.revoked is False
                assert str(request.url) == GROQ_BASE_URL + "/openai/v1/chat/completions"
                assert request.headers["authorization"] == "Bearer " + GROQ_KEY
                assert "x-api-key" not in request.headers
                assert json.loads(request.content)["model"] == MODEL
                for secret in (TOKEN, GROQ_KEY, PLANNING_KEY, DB_KEY):
                    assert secret not in request.content.decode()
                return httpx.Response(200, json=completion())
            assert request.url.host == "storage.example.test"
            assert request.url.path == "/rest/v1/monitoring_reports"
            assert request.method == "POST"
            assert request.headers["apikey"] == DB_KEY
            assert "authorization" not in request.headers
            assert GROQ_KEY not in str(request.headers)
            events.append("save")
            rows.append(json.loads(request.content))
            return httpx.Response(201)

        constructor = httpx.AsyncClient

        def factory(**options):
            options_seen.append(dict(options))
            if "transport" not in options:
                options["transport"] = httpx.MockTransport(network)
            client = constructor(**options)
            clients.append(client)
            return client

        monkeypatch.setattr(api.httpx, "AsyncClient", factory)
        settings = api.MonitoringAPISettings(
            groq_api_key=SecretStr(GROQ_KEY), groq_model=MODEL,
            planning_api_key=SecretStr(PLANNING_KEY),
            storage=api.SupabaseMonitoringSettings(
                url="https://storage.example.test", secret_key=SecretStr(DB_KEY),
            ),
        )
        service.include_router(api.create_shared_router(service, settings))
        with TestClient(service) as client:
            yield SimpleNamespace(client=client, access=access, events=events, requests=requests,
                                  rows=rows, clients=clients, options=options_seen)
        assert internal_groq == []
        assert all(client.is_closed for client in clients)

    return build


def test_shared_ownership_revoked_after_risk_blocks_groq_disclosure_and_save(shared_groq_api):
    with shared_groq_api(revoke_after_risk=True) as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS,
                                       json=request_body(include_risks=True, save_report=True))
        assert response.status_code == 403
        assert response.json() == {"detail": "Insufficient Monitoring project access"}
        assert service.events[:6] == [
            "principal", "recheck:1", "planning", "recheck:2", "risk", "recheck:3",
        ]
        assert service.access.revoked is True
        assert service.access.checks >= 3
        assert service.requests == service.rows == []
        assert "groq" not in service.events
        assert "save" not in service.events
        assert_no_credentials(response)


def test_shared_rechecks_before_external_groq_and_keeps_risk_on_internal_transport(shared_groq_api):
    with shared_groq_api() as service:
        response = service.client.post(ROOT + "/reports", headers=HEADERS,
                                       json=request_body(include_risks=True, save_report=True))
        assert response.status_code == 200
        report = response.json()
        assert report["summary_method"] == "llm_extractive"
        assert report["coverage"]["risk_status"] == "available"
        assert report["risks"][0]["risk_id"] == "R001"
        assert FALLBACK not in report["limitations"]
        assert service.events[:7] == [
            "principal", "recheck:1", "planning", "recheck:2", "risk", "recheck:3", "groq",
        ]
        assert service.events[service.events.index("save") - 1] == "recheck:4"
        assert [request.url.host for request in service.requests] == ["api.groq.com", "storage.example.test"]
        assert len(service.rows) == 1
        assert service.rows[0]["report"] == report
        internal = [options for options in service.options if options.get("base_url") == "http://127.0.0.1"]
        external = [options for options in service.options if options.get("base_url") == GROQ_BASE_URL]
        assert len(internal) == len(external) == 1
        assert isinstance(internal[0]["transport"], httpx.ASGITransport)
        assert "transport" not in external[0]
        assert_no_credentials(response)
        for secret in (TOKEN, GROQ_KEY, PLANNING_KEY, DB_KEY):
            assert secret not in json.dumps(service.rows)


def test_settings_defaults_and_secret_repr():
    settings = api.MonitoringAPISettings()
    assert settings.groq_api_key is None
    assert settings.groq_model == MODEL
    assert settings.llm_enabled is True
    configured = api.MonitoringAPISettings(groq_api_key=SecretStr(GROQ_KEY))
    assert isinstance(configured.groq_api_key, SecretStr)
    assert next(field for field in fields(configured) if field.name == "groq_api_key").repr is False
    assert "groq_api_key" not in repr(configured)
    assert GROQ_KEY not in repr(configured)


@pytest.mark.parametrize("value, enabled", [("true", True), ("false", False), ("1", True), ("0", False)])
def test_from_env_reads_synthetic_groq_configuration(tmp_path, value, enabled):
    path = tmp_path / "isolated-groq.env"
    contents = f"GROQ_API_KEY={GROQ_KEY}\nGROQ_MODEL=synthetic-file-model\nMONITORING_LLM_ENABLED={value}\n"
    path.write_text(contents, encoding="utf-8")
    settings = api.MonitoringAPISettings.from_env(path)
    assert isinstance(settings.groq_api_key, SecretStr)
    assert settings.groq_api_key.get_secret_value() == GROQ_KEY
    assert settings.groq_model == "synthetic-file-model"
    assert settings.llm_enabled is enabled
    assert api.os.environ == {}
    assert path.read_text(encoding="utf-8") == contents
    assert GROQ_KEY not in repr(settings)


@pytest.mark.parametrize("file_flag, process_flag, enabled", [("true", "0", False), ("false", "1", True)])
def test_from_env_process_values_override_file_without_mutation(tmp_path, file_flag, process_flag, enabled):
    path = tmp_path / "isolated-groq.env"
    path.write_text(f"GROQ_API_KEY=synthetic-file-key\nGROQ_MODEL=synthetic-file-model\n"
                    f"MONITORING_LLM_ENABLED={file_flag}\n", encoding="utf-8")
    process = {"GROQ_API_KEY": GROQ_KEY, "GROQ_MODEL": "synthetic-process-model",
               "MONITORING_LLM_ENABLED": process_flag}
    api.os.environ.update(process)
    settings = api.MonitoringAPISettings.from_env(path)
    assert settings.groq_api_key.get_secret_value() == GROQ_KEY
    assert settings.groq_model == "synthetic-process-model"
    assert settings.llm_enabled is enabled
    assert api.os.environ == process
    assert GROQ_KEY not in repr(settings)


def test_from_env_absent_groq_values_use_defaults(tmp_path):
    settings = api.MonitoringAPISettings.from_env(tmp_path / "absent.env")
    assert settings.groq_api_key is None
    assert settings.groq_model == MODEL
    assert settings.llm_enabled is True


@pytest.mark.parametrize("source", ["file", "process"])
@pytest.mark.parametrize("value", ["yes", "2", "false-ish", "synthetic-invalid-flag-secret"])
def test_from_env_rejects_invalid_enabled_flag_without_echo(tmp_path, source, value):
    path = tmp_path / "isolated-groq.env"
    path.write_text(f"GROQ_API_KEY={GROQ_KEY}\nMONITORING_LLM_ENABLED="
                    + (value if source == "file" else "true") + "\n", encoding="utf-8")
    if source == "process":
        api.os.environ["MONITORING_LLM_ENABLED"] = value
    with pytest.raises(api.MonitoringAPIConfigurationError) as exc:
        api.MonitoringAPISettings.from_env(path)
    assert value not in str(exc.value)
    assert GROQ_KEY not in str(exc.value)
