"""Isolated project-access regressions: no real agents, settings readers, or DB."""

from __future__ import annotations

import builtins
import importlib.util
import io
import os
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast as type_cast
from unittest.mock import Mock

import dotenv
import dotenv.main
import httpx
import pydantic_settings.sources.providers.dotenv as settings_dotenv
import pytest
import sqlalchemy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from pydantic_settings.sources.providers.env import EnvSettingsSource
from sqlalchemy import Column, Integer, MetaData, String, Table, cast
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ClauseElement


BACKEND = Path(__file__).resolve().parents[1]
PROJECT = "test-project-a"
OTHER_PROJECT = "test-project-b"
DOCUMENT = "test-document-shared"
SEARCH_PATH = "/api/risk/knowledge/search"


def deny_external_initialization(*args, **kwargs):
    raise AssertionError("Project authorization tests must not load settings or use external IO")


def guarded_open(reader):
    def open_without_environment_files(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            parts = os.fsdecode(file).replace("\\", "/").lower().split("/")
            if any(part == ".env" or part.startswith(".env.") for part in parts):
                raise AssertionError("Protected environment files must not be opened")
        return reader(file, *args, **kwargs)
    return open_without_environment_files


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    for module in (dotenv, dotenv.main):
        for name in ("load_dotenv", "dotenv_values", "find_dotenv"):
            monkeypatch.setattr(module, name, deny_external_initialization)
    monkeypatch.setattr(dotenv.main.DotEnv, "_get_stream", deny_external_initialization)
    monkeypatch.setattr(settings_dotenv, "read_env_file", deny_external_initialization)
    for name in ("_read_env_files", "_read_env_file", "_static_read_env_file"):
        monkeypatch.setattr(settings_dotenv.DotEnvSettingsSource, name, deny_external_initialization)
    monkeypatch.setattr(EnvSettingsSource, "_load_env_vars", deny_external_initialization)
    monkeypatch.setattr(builtins, "open", guarded_open(builtins.open))
    monkeypatch.setattr(io, "open", guarded_open(io.open))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_external_initialization)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_external_initialization)
    monkeypatch.setattr(sqlalchemy, "create_engine", deny_external_initialization)


def stub_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def load_source(monkeypatch, name, relative_path):
    # Private module names avoid replacing another test's live application modules.
    spec = importlib.util.spec_from_file_location(name, BACKEND / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def synthetic_settings(monkeypatch):
    settings = SimpleNamespace(
        admin_username="test-project-administrator",
        admin_password="synthetic-password-not-a-runtime-credential",
        admin_password_hash=None,
        jwt_secret_key="synthetic-project-test-signing-material-" + "x" * 64,
        jwt_algorithm="HS256",
        jwt_issuer="test-project-authorization",
        jwt_audience="test-project-administration",
        access_token_expire_minutes=10,
        postgres_search_limit=5,
    )
    stub_module(
        monkeypatch, "app.core.config", settings=settings,
        get_settings=lambda: settings, Settings=deny_external_initialization,
    )
    return settings


class SearchRequest(BaseModel):
    project_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)


class UnusedAgentRequest(BaseModel):
    project_id: str


class FakeCollection:
    def __init__(self):
        self.calls = []
        self.records = [
            ("a-shared-1", "Second project A chunk", PROJECT, DOCUMENT, 1),
            ("a-shared-0", "First project A chunk", PROJECT, DOCUMENT, 0),
            ("a-other-0", "Another project A document", PROJECT, "test-document-a-only", 0),
            ("b-shared-0", "Project B must remain separate", OTHER_PROJECT, DOCUMENT, 0),
            ("b-only-0", "Project B only document", OTHER_PROJECT, "test-document-b-only", 0),
        ]

    def get(self, *, where, include):
        self.calls.append({"where": deepcopy(where), "include": list(include)})
        conditions = where.get("$and", [where])
        result = {"ids": [], "documents": [], "metadatas": []}
        for chunk_id, text, project, document, index in self.records:
            metadata = {
                "project_id": project, "document_id": document,
                "chunk_index": index, "source": document + ".pdf",
            }
            if all(all(metadata.get(key) == value for key, value in condition.items())
                   for condition in conditions):
                result["ids"].append(chunk_id)
                result["documents"].append(text)
                result["metadatas"].append(metadata)
        return result


@pytest.fixture
def risk_api(monkeypatch, synthetic_settings):
    security = load_source(monkeypatch, "offline_project_security", "app/core/security.py")
    stub_module(monkeypatch, "app.core.security", require_admin=security.require_admin)
    collection = FakeCollection()
    agent = SimpleNamespace(
        search_project_knowledge=Mock(),
        retrieval_service=SimpleNamespace(collection=collection),
    )
    stub_module(monkeypatch, "app.agents.risk_knowledge.agent", RiskKnowledgeAgent=lambda: agent)
    stub_module(
        monkeypatch, "app.agents.risk_knowledge.schemas",
        ProjectKnowledgeSearchRequest=SearchRequest,
        ProjectPlanAnalysisRequest=UnusedAgentRequest,
        RiskAnalysisResult=UnusedAgentRequest,
    )
    stub_module(
        monkeypatch, "app.communication.messages",
        KnowledgeSearchMessage=UnusedAgentRequest,
        ProjectPlanMessage=UnusedAgentRequest, RiskRequestMessage=UnusedAgentRequest,
    )
    stub_module(monkeypatch, "app.communication.protocol", RiskAgentProtocol=lambda **kwargs: SimpleNamespace())
    risks = load_source(monkeypatch, "offline_project_risks", "app/api/risks.py")
    application = FastAPI()
    application.include_router(risks.router)
    with TestClient(application) as client:
        yield SimpleNamespace(
            client=client, module=risks, agent=agent, collection=collection,
            security=security, settings=synthetic_settings,
        )


def bearer(api, role="admin", subject=None):
    token = api.security.create_access_token(subject or api.settings.admin_username, role)
    return {"Authorization": "Bearer " + token}


@pytest.mark.parametrize("identity,status", [
    ("anonymous", 401), ("nonadmin", 403), ("different_admin", 403), ("invalid", 401),
])
def test_search_denied_before_agent(risk_api, identity, status):
    headers = {
        "anonymous": {}, "nonadmin": bearer(risk_api, role="member"),
        "different_admin": bearer(risk_api, subject="test-other-administrator"),
        "invalid": {"Authorization": "Bearer invalid-synthetic-token"},
    }[identity]
    response = risk_api.client.post(
        SEARCH_PATH, headers=headers,
        json={"project_id": PROJECT, "query": "review dependencies", "top_k": 5},
    )
    assert response.status_code == status
    risk_api.agent.search_project_knowledge.assert_not_called()
    assert risk_api.collection.calls == []


@pytest.mark.parametrize("include_top_k", [False, True])
def test_configured_administrator_keeps_search_payload_and_shape(risk_api, include_top_k):
    payload: dict[str, str | int] = {"project_id": PROJECT, "query": "review dependencies"}
    if include_top_k:
        payload["top_k"] = 5
    expected = {
        "project_id": PROJECT, "results": [{
            "knowledge_type": "document", "source": "test-review.pdf",
            "chunk_id": "test-review-chunk", "score": 0.8,
            "content": "Recorded dependency review",
            "metadata": {"project_id": PROJECT, "document_id": DOCUMENT},
        }],
    }
    result = Mock()
    result.model_dump.return_value = expected
    risk_api.agent.search_project_knowledge.return_value = result
    response = risk_api.client.post(SEARCH_PATH, headers=bearer(risk_api), json=payload)
    assert response.status_code == 200
    assert response.json() == expected
    risk_api.agent.search_project_knowledge.assert_called_once_with(
        project_id=PROJECT, query=payload["query"], top_k=5,
    )
    result.model_dump.assert_called_once_with()


@pytest.mark.parametrize("path", [
    "/api/risk/knowledge/documents",
    f"/api/risk/knowledge/documents/{DOCUMENT}/chunks",
])
@pytest.mark.parametrize("role,status", [(None, 401), ("member", 403)])
def test_existing_document_routes_remain_admin_gated(risk_api, path, role, status):
    headers = {} if role is None else bearer(risk_api, role=role)
    response = risk_api.client.get(path, params={"project_id": PROJECT}, headers=headers)
    assert response.status_code == status
    assert risk_api.collection.calls == []


def test_document_list_uses_stored_project_filter(risk_api):
    response = risk_api.client.get(
        "/api/risk/knowledge/documents", params={"project_id": " " + PROJECT + " "},
        headers=bearer(risk_api),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == PROJECT
    assert body["total_documents"] == 2 and body["total_chunks"] == 3
    assert {item["document_id"] for item in body["documents"]} == {DOCUMENT, "test-document-a-only"}
    assert all(item["project_id"] == PROJECT for item in body["documents"])
    assert risk_api.collection.calls == [{"where": {"project_id": PROJECT}, "include": ["metadatas"]}]


def test_chunk_lookup_requires_project_and_document(risk_api):
    response = risk_api.client.get(
        f"/api/risk/knowledge/documents/{DOCUMENT}/chunks",
        params={"project_id": " " + PROJECT + " "}, headers=bearer(risk_api),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == PROJECT and body["document_id"] == DOCUMENT
    assert body["total_chunks"] == 2
    assert [chunk["chunk_id"] for chunk in body["chunks"]] == ["a-shared-0", "a-shared-1"]
    assert all(chunk["metadata"]["project_id"] == PROJECT for chunk in body["chunks"])
    assert risk_api.collection.calls == [{
        "where": {"$and": [{"project_id": PROJECT}, {"document_id": DOCUMENT}]},
        "include": ["documents", "metadatas"],
    }]


@pytest.mark.parametrize("project,document", [
    (PROJECT, "test-document-b-only"), (OTHER_PROJECT, "test-document-a-only"),
])
def test_cross_project_document_pair_returns_not_found(risk_api, project, document):
    response = risk_api.client.get(
        f"/api/risk/knowledge/documents/{document}/chunks",
        params={"project_id": project}, headers=bearer(risk_api),
    )
    assert response.status_code == 404
    assert risk_api.collection.calls[0]["where"] == {
        "$and": [{"project_id": project}, {"document_id": document}],
    }


@pytest.mark.parametrize("path", ["/analyse", "/analyse-plan", "/agent/plan"])
def test_source_route_admin_policy_is_unchanged(risk_api, path):
    route = next(route for route in risk_api.module.router.routes if route.path == "/api/risk" + path)
    assert all(dependency.call is not risk_api.security.require_admin
               for dependency in route.dependant.dependencies)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        self.statements.append(statement)
        rows = self.rows.get(statement.get_final_froms()[0].name, [])
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: deepcopy(rows)))


@pytest.fixture
def postgres_module(monkeypatch, synthetic_settings):
    return load_source(monkeypatch, "offline_project_postgres", "app/services/postgres_service.py")


def fake_service(module, tables, rows):
    cursor = FakeCursor(rows)
    service = object.__new__(module.PostgreSQLService)
    service.engine = SimpleNamespace(connect=lambda: cursor)
    service._get_tables = lambda: tables
    return service, cursor


def bound_table(column="project_id", column_type=None, name="test_bound_plans"):
    return Table(
        name, MetaData(), Column("id", Integer, primary_key=True),
        Column(column, column_type or String), Column("content", String),
    )


def assert_project_predicate(statement, table, column, project):
    expected = cast(table.c[column], String) == str(project)
    assert any(isinstance(node, ClauseElement) and node.compare(expected)
               for node in visitors.iterate(statement.whereclause))
    compiled = statement.compile(dialect=postgresql.dialect())
    assert str(project) in compiled.params.values()
    return compiled


@pytest.mark.parametrize("nonbinding_column", ["project_name", "project_ids", "document_id", "metadata"])
def test_postgres_skips_tables_without_genuine_project_column(postgres_module, nonbinding_column):
    table = Table(
        "test_unbound_records", MetaData(), Column("id", Integer, primary_key=True),
        Column("content", String), Column(nonbinding_column, String),
    )
    rows = [{"id": 1, "content": "Unbound evidence must not be attributed", nonbinding_column: PROJECT}]
    service, cursor = fake_service(postgres_module, [table], {table.name: rows})
    assert service.search_project_knowledge(PROJECT, "evidence") == []
    assert cursor.statements == []


@pytest.mark.parametrize("column", ["project_id", "projectid", "project", "PROJECT_ID", "ProjectId"])
def test_postgres_bound_queries_keep_project_predicate_and_provenance(postgres_module, column):
    table = bound_table(column)
    row = {"id": 7, column: PROJECT, "content": "Recorded review evidence"}
    service, cursor = fake_service(postgres_module, [table], {table.name: [row]})
    results = service.search_project_knowledge(PROJECT, "review")
    assert len(cursor.statements) == len(results) == 1
    compiled = assert_project_predicate(cursor.statements[0], table, column, PROJECT)
    assert "%review%" in compiled.params.values()
    assert results[0].table_name == table.name and results[0].record_id == "7"
    assert results[0].content == f"{column}: {PROJECT}\ncontent: Recorded review evidence"
    assert results[0].metadata == {
        "project_id": row[column], "table": table.name, "record_id": "7", "knowledge_type": "planning_agent",
    }


def test_postgres_never_relabels_unbound_rows_among_bound_results(postgres_module):
    bound = bound_table()
    unbound = Table(
        "test_unbound_evidence", MetaData(), Column("id", Integer, primary_key=True),
        Column("content", String),
    )
    service, cursor = fake_service(postgres_module, [unbound, bound], {
        unbound.name: [{"id": 1, "content": "Foreign unbound evidence"}],
        bound.name: [{"id": 2, "project_id": PROJECT, "content": "Bound review evidence"}],
    })
    results = service.search_project_knowledge(PROJECT, "evidence")
    assert [statement.get_final_froms()[0].name for statement in cursor.statements] == [bound.name]
    assert [result.table_name for result in results] == [bound.name]
    assert [result.metadata["project_id"] for result in results] == [PROJECT]
    assert all("Foreign unbound evidence" not in result.content for result in results)


def test_postgres_keeps_values_parameterized_and_identifiers_quoted(postgres_module):
    table = bound_table(name='test_quoted"plans')
    project = "test-project' OR '1'='1"
    query = "review' OR '1'='1"
    service, cursor = fake_service(postgres_module, [table], {table.name: []})
    assert service.search_project_knowledge(project, query, limit=3) == []
    statement = cursor.statements[0]
    compiled = assert_project_predicate(statement, table, "project_id", project)
    sql = str(compiled)
    assert project not in sql and query not in sql
    assert "%" + query + "%" in compiled.params.values()
    assert 3 in compiled.params.values()
    assert '"test_quoted""plans"' in sql


def test_postgres_preserves_integer_project_column_cast(postgres_module):
    table = bound_table(column_type=Integer)
    service, cursor = fake_service(postgres_module, [table], {
        table.name: [{"id": 3, "project_id": 42, "content": "Integer-bound evidence"}],
    })
    results = service.search_project_knowledge("42", "evidence")
    assert_project_predicate(cursor.statements[0], table, "project_id", "42")
    assert results[0].metadata["project_id"] == "42"
    assert results[0].content == "content: Integer-bound evidence"


def test_offline_settings_and_dotenv_readers_are_guarded():
    readers: tuple[Callable[..., object], ...] = (
        dotenv.load_dotenv, dotenv.dotenv_values, dotenv.find_dotenv,
        dotenv.main.DotEnv._get_stream, settings_dotenv.read_env_file,
        settings_dotenv.DotEnvSettingsSource._read_env_files,
        EnvSettingsSource._load_env_vars,
    )
    for reader in readers:
        with pytest.raises(AssertionError, match="must not load settings"):
            type_cast(Callable[..., object], reader)()
