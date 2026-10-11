"""Offline ownership contracts: fake schedules/storage, no runtime settings or SDK clients."""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import io
import os
import re
import socket
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import dotenv
import dotenv.main
import httpx
import pytest
from pydantic_settings.sources.providers import dotenv as settings_dotenv
from pydantic_settings.sources.providers.env import EnvSettingsSource


BACKEND = Path(__file__).resolve().parents[1]
OWNER = "00000000-0000-4000-8000-000000000001"
OTHER_OWNER = "00000000-0000-4000-8000-000000000002"
PROJECT = "owned-project"
OTHER_PROJECT = "another-owner-project"
LEGACY_PROJECT = "unassigned-project"
MISSING_PROJECT = "missing-project"
DEFAULT_RESPONSE = object()


def deny_external(*args, **kwargs):
    raise AssertionError("Offline ownership tests prohibit settings/network/client initialization")


def protected_open(reader):
    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            parts = os.fsdecode(file).replace("\\", "/").lower().split("/")
            if any(
                part == ".env" or part.startswith(".env.")
                or part in {"credentials", "credentials.json", "secrets.json", "id_rsa", "id_ed25519"}
                or part.endswith((".key", ".pem"))
                for part in parts
            ):
                raise AssertionError("Protected private file")
        return reader(file, *args, **kwargs)
    return guarded


@pytest.fixture(autouse=True)
def offline_guards(monkeypatch):
    # Install these before any application source is imported, not just before requests.
    with patch.dict(os.environ, {}, clear=True):
        for module in (dotenv, dotenv.main):
            for name in ("load_dotenv", "dotenv_values", "find_dotenv"):
                monkeypatch.setattr(module, name, deny_external)
        monkeypatch.setattr(dotenv.main.DotEnv, "_get_stream", deny_external)
        monkeypatch.setattr(settings_dotenv, "read_env_file", deny_external)
        for name in ("_read_env_files", "_read_env_file", "_static_read_env_file"):
            monkeypatch.setattr(settings_dotenv.DotEnvSettingsSource, name, deny_external)
        monkeypatch.setattr(EnvSettingsSource, "_load_env_vars", deny_external)
        monkeypatch.setattr(builtins, "open", protected_open(builtins.open))
        monkeypatch.setattr(io, "open", protected_open(io.open))
        monkeypatch.setattr(os, "open", protected_open(os.open))
        monkeypatch.setattr(socket, "create_connection", deny_external)
        monkeypatch.setattr(socket, "getaddrinfo", deny_external)
        monkeypatch.setattr(socket.socket, "connect", deny_external)
        monkeypatch.setattr(socket.socket, "connect_ex", deny_external)
        monkeypatch.setattr(httpx.Client, "__init__", deny_external)
        monkeypatch.setattr(httpx.AsyncClient, "__init__", deny_external)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_external)
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_external)
        yield


def stub_module(monkeypatch, name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, module)
    return module


@dataclass(frozen=True)
class FakeSchedule:
    """Only the repository's serialization interface; no planning models or algorithms."""
    project_id: str
    project_name: str
    schedule: list[dict] = field(default_factory=lambda: [{"task_id": "offline-task", "duration_days": 2}])
    warnings: list[str] = field(default_factory=list)

    def model_dump(self, *, mode):
        assert mode == "json"
        return asdict(self)

    @classmethod
    def model_validate(cls, value):
        return cls(**deepcopy(value))


def stored_row(project_id, owner_user_id, created_at):
    schedule = FakeSchedule(project_id, project_id + " name")
    return {
        "project_id": project_id,
        "project_name": schedule.project_name,
        "schedule": schedule.model_dump(mode="json"),
        "owner_user_id": owner_user_id,
        "created_at": created_at,
    }


class FakeClient:
    def __init__(self, api_error):
        self.api_error = api_error
        self.rows = {
            PROJECT: stored_row(PROJECT, OWNER, 1),
            OTHER_PROJECT: stored_row(OTHER_PROJECT, OTHER_OWNER, 2),
            LEGACY_PROJECT: stored_row(LEGACY_PROJECT, None, 3),
        }
        self.calls = []
        self.failure = None
        self.before_execute = None
        self.response_data = DEFAULT_RESPONSE

    def table(self, name):
        assert name == "project_schedules"
        return FakeQuery(self)


class FakeQuery:
    """Evaluate predicates at execute(), including predicates on actual writes."""
    def __init__(self, client):
        self.client = client
        self.operation = None
        self.payload = None
        self.filters = []
        self.columns = None
        self.ordering = None
        self.row_limit = None
        self.on_conflict = None

    def select(self, columns):
        self.operation = "select"
        self.columns = columns
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def upsert(self, payload, *, on_conflict):
        self.operation = "upsert"
        self.payload = deepcopy(payload)
        self.on_conflict = on_conflict
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, column, *, desc):
        self.ordering = (column, desc)
        return self

    def limit(self, count):
        self.row_limit = count
        return self

    def execute(self):
        self.client.calls.append({
            "operation": self.operation, "payload": deepcopy(self.payload),
            "filters": list(self.filters), "columns": self.columns,
            "ordering": self.ordering, "limit": self.row_limit,
            "on_conflict": self.on_conflict,
        })
        if self.client.before_execute is not None:
            callback = self.client.before_execute
            self.client.before_execute = None
            callback(self)
        if self.client.failure is not None:
            raise self.client.failure

        if self.operation in {"insert", "upsert"}:
            assert self.payload is not None
            project_id = self.payload["project_id"]
            if self.operation == "insert" and project_id in self.client.rows:
                raise self.client.api_error({
                    "message": "Synthetic duplicate project", "code": "23505",
                    "details": None, "hint": None,
                })
            row = self.client.rows.setdefault(project_id, {
                "project_id": project_id, "owner_user_id": None,
                "created_at": len(self.client.rows) + 1,
            })
            row.update(deepcopy(self.payload))
            data = [deepcopy(row)]
        else:
            matches = [row for row in self.client.rows.values()
                       if all(row.get(column) == value for column, value in self.filters)]
            if self.ordering is not None:
                column, desc = self.ordering
                matches.sort(key=lambda row: row[column], reverse=desc)
            if self.row_limit is not None:
                matches = matches[:self.row_limit]
            if self.operation == "select":
                assert self.columns is not None
                data = [{column: deepcopy(row[column]) for column in self.columns.split(",")}
                        for row in matches]
            elif self.operation == "update":
                assert self.payload is not None
                for row in matches:
                    row.update(deepcopy(self.payload))
                data = deepcopy(matches)
            elif self.operation == "delete":
                data = deepcopy(matches)
                for row in matches:
                    del self.client.rows[row["project_id"]]
            else:
                raise AssertionError("Unexpected storage operation")

        if self.client.response_data is not DEFAULT_RESPONSE:
            data = self.client.response_data
        return SimpleNamespace(data=data)


@pytest.fixture
def runtime(monkeypatch, offline_guards):
    # Real error objects, but never a PostgREST/Supabase client or actual app settings/model.
    exceptions = importlib.import_module("postgrest.exceptions")
    postgrest = importlib.import_module("postgrest")
    for name in ("SyncPostgrestClient", "AsyncPostgrestClient"):
        monkeypatch.setattr(postgrest, name, deny_external)
    settings = SimpleNamespace(
        supabase_url="https://offline.invalid", supabase_secret_key="synthetic-service-role",
    )
    stub_module(monkeypatch, "app.core.config", get_settings=lambda: settings, Settings=deny_external)
    stub_module(monkeypatch, "app.model.planning", ScheduleResponse=FakeSchedule)
    stub_module(monkeypatch, "supabase", Client=deny_external, create_client=deny_external)
    name = "offline_planning_ownership_repository"
    spec = importlib.util.spec_from_file_location(name, BACKEND / "app/repository/planning_repository.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    repository = module.PlanningRepository()
    client = FakeClient(exceptions.APIError)
    repository._client = client
    return SimpleNamespace(module=module, repository=repository, client=client, settings=settings)


def assert_call(runtime, operation, filters):
    assert len(runtime.client.calls) == 1
    call = runtime.client.calls[0]
    assert call["operation"] == operation
    assert call["filters"] == filters
    return call


def no_legacy_read_or_save(monkeypatch, repository):
    for name in ("get", "get_all", "save"):
        monkeypatch.setattr(repository, name, deny_external)


def assert_private_reader_blocked(reader, path, *args):
    with pytest.raises(AssertionError, match="Protected private file"):
        reader(path, *args)


def test_offline_guards_are_active_before_runtime_imports(runtime, tmp_path):
    assert set(os.environ) <= {"PYTEST_CURRENT_TEST"}
    for name in (".env", ".env.example", "credentials.json", "private.key"):
        path = tmp_path / name
        assert_private_reader_blocked(builtins.open, path)
        assert_private_reader_blocked(io.open, path)
        assert_private_reader_blocked(os.open, path, os.O_RDONLY)
    for action in (
        lambda: dotenv.load_dotenv(), lambda: dotenv.dotenv_values(), lambda: dotenv.find_dotenv(),
        lambda: dotenv.main.load_dotenv(), lambda: dotenv.main.dotenv_values(), lambda: dotenv.main.find_dotenv(),
        lambda: settings_dotenv.read_env_file(tmp_path / ".env"),
        lambda: EnvSettingsSource._load_env_vars(EnvSettingsSource.__new__(EnvSettingsSource)),
        lambda: settings_dotenv.DotEnvSettingsSource._read_env_files(
            settings_dotenv.DotEnvSettingsSource.__new__(settings_dotenv.DotEnvSettingsSource),
        ),
        lambda: socket.create_connection(("offline.invalid", 443)),
        lambda: socket.getaddrinfo("offline.invalid", 443),
        lambda: httpx.Client(), lambda: httpx.AsyncClient(),
        lambda: runtime.module.create_client("https://offline.invalid", "synthetic-service-role"),
    ):
        with pytest.raises(AssertionError, match="Offline ownership tests"):
            action()
    assert runtime.module.ScheduleResponse is FakeSchedule
    repository = runtime.module.PlanningRepository()
    with pytest.raises(AssertionError, match="Offline ownership tests"):
        _ = repository.client
    assert repository._client is None


@pytest.mark.parametrize("owner,expected", [(OWNER, [PROJECT]), (OTHER_OWNER, [OTHER_PROJECT])])
def test_regular_lists_are_filtered_in_storage(runtime, owner, expected):
    schedules = runtime.repository.get_all(owner_user_id=owner)
    assert [schedule.project_id for schedule in schedules] == expected
    call = assert_call(runtime, "select", [("owner_user_id", owner)])
    assert call["columns"] == "schedule"
    assert call["ordering"] == ("created_at", True)


def test_trusted_list_includes_all_owned_and_unassigned_rows(runtime):
    assert [schedule.project_id for schedule in runtime.repository.get_all()] == [
        LEGACY_PROJECT, OTHER_PROJECT, PROJECT,
    ]
    assert_call(runtime, "select", [])


@pytest.mark.parametrize("project_id,found", [
    (PROJECT, True), (OTHER_PROJECT, False), (LEGACY_PROJECT, False), (MISSING_PROJECT, False),
])
def test_regular_reads_filter_project_and_owner_in_storage(runtime, project_id, found):
    result = runtime.repository.get(project_id, owner_user_id=OWNER)
    assert (result is not None) is found
    if found:
        assert result == FakeSchedule(project_id, project_id + " name")
    call = assert_call(runtime, "select", [("project_id", project_id), ("owner_user_id", OWNER)])
    assert call["limit"] == 1


@pytest.mark.parametrize("project_id", [PROJECT, OTHER_PROJECT, LEGACY_PROJECT])
def test_trusted_reads_include_unassigned_and_other_owners(runtime, project_id):
    assert runtime.repository.get(project_id, owner_user_id=None).project_id == project_id
    assert_call(runtime, "select", [("project_id", project_id)])


def test_exists_forwards_owner_scope(runtime):
    assert runtime.repository.exists(OTHER_PROJECT, owner_user_id=OWNER) is False
    assert_call(runtime, "select", [("project_id", OTHER_PROJECT), ("owner_user_id", OWNER)])


@pytest.mark.parametrize("owner", [OWNER, None])
def test_create_is_one_insert_with_server_derived_owner(runtime, monkeypatch, owner):
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    schedule = FakeSchedule(MISSING_PROJECT, "New project")
    assert runtime.repository.create(schedule, owner_user_id=owner) is schedule
    call = assert_call(runtime, "insert", [])
    assert call["payload"] == {
        "project_id": schedule.project_id, "project_name": schedule.project_name,
        "schedule": schedule.model_dump(mode="json"), "owner_user_id": owner,
    }
    assert call["on_conflict"] is None
    assert runtime.client.rows[MISSING_PROJECT]["owner_user_id"] == owner


def test_create_requires_explicit_keyword_owner(runtime):
    schedule = FakeSchedule(MISSING_PROJECT, "New project")
    with pytest.raises(TypeError):
        runtime.repository.create(schedule)
    with pytest.raises(TypeError):
        runtime.repository.create(schedule, OWNER)
    assert runtime.client.calls == []


@pytest.mark.parametrize("project_id", [PROJECT, OTHER_PROJECT, LEGACY_PROJECT])
def test_duplicate_insert_never_overwrites_or_claims_existing_rows(runtime, monkeypatch, project_id):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    with pytest.raises(runtime.module.ProjectScheduleExists) as caught:
        runtime.repository.create(FakeSchedule(project_id, "Replacement"), owner_user_id=OWNER)
    assert caught.value.__cause__.code == "23505"
    assert runtime.client.rows == before
    assert_call(runtime, "insert", [])


@pytest.mark.parametrize("project_id,scope", [
    (PROJECT, OWNER), (PROJECT, None), (OTHER_PROJECT, None), (LEGACY_PROJECT, None),
])
def test_update_is_conditional_and_preserves_stored_owner(runtime, monkeypatch, project_id, scope):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    schedule = FakeSchedule(project_id, "Updated", warnings=["Offline review"])
    assert runtime.repository.update(project_id, schedule, owner_user_id=scope) is schedule
    filters = [("project_id", project_id)]
    if scope is not None:
        filters.append(("owner_user_id", scope))
    call = assert_call(runtime, "update", filters)
    assert call["payload"] == {"project_name": "Updated", "schedule": schedule.model_dump(mode="json")}
    assert runtime.client.rows[project_id]["owner_user_id"] == before[project_id]["owner_user_id"]
    assert runtime.client.rows[project_id]["schedule"] == schedule.model_dump(mode="json")
    for other_id in before.keys() - {project_id}:
        assert runtime.client.rows[other_id] == before[other_id]


@pytest.mark.parametrize("project_id", [OTHER_PROJECT, LEGACY_PROJECT, MISSING_PROJECT])
def test_update_no_match_returns_none_and_does_not_insert(runtime, monkeypatch, project_id):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    assert runtime.repository.update(
        project_id, FakeSchedule(project_id, "Denied"), owner_user_id=OWNER,
    ) is None
    assert runtime.client.rows == before
    assert_call(runtime, "update", [("project_id", project_id), ("owner_user_id", OWNER)])


def test_update_rejects_project_id_mismatch_before_storage(runtime):
    with pytest.raises(ValueError, match="does not match"):
        runtime.repository.update(PROJECT, FakeSchedule(OTHER_PROJECT, "Wrong"), owner_user_id=OWNER)
    assert runtime.client.calls == []


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_owner_reassignment_before_actual_write_prevents_stale_access(runtime, monkeypatch, operation):
    before = deepcopy(runtime.client.rows[PROJECT])
    no_legacy_read_or_save(monkeypatch, runtime.repository)

    def reassign_before_write(query):
        assert query.operation == operation
        assert query.filters == [("project_id", PROJECT), ("owner_user_id", OWNER)]
        runtime.client.rows[PROJECT]["owner_user_id"] = OTHER_OWNER

    runtime.client.before_execute = reassign_before_write
    if operation == "update":
        result = runtime.repository.update(PROJECT, FakeSchedule(PROJECT, "Stale edit"), owner_user_id=OWNER)
        assert result is None
    else:
        assert runtime.repository.delete(PROJECT, owner_user_id=OWNER) is False
    assert runtime.client.rows[PROJECT] == {**before, "owner_user_id": OTHER_OWNER}
    assert_call(runtime, operation, [("project_id", PROJECT), ("owner_user_id", OWNER)])


def test_trusted_update_does_not_reset_owner_reassigned_at_write(runtime):
    runtime.client.before_execute = lambda query: runtime.client.rows[PROJECT].update(owner_user_id=OTHER_OWNER)
    schedule = FakeSchedule(PROJECT, "Trusted edit")
    assert runtime.repository.update(PROJECT, schedule) is schedule
    assert runtime.client.rows[PROJECT]["owner_user_id"] == OTHER_OWNER
    assert "owner_user_id" not in assert_call(runtime, "update", [("project_id", PROJECT)])["payload"]


def test_regular_delete_is_conditioned_at_storage_write(runtime, monkeypatch):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    assert runtime.repository.delete(PROJECT, owner_user_id=OWNER) is True
    assert runtime.client.rows == {key: row for key, row in before.items() if key != PROJECT}
    assert_call(runtime, "delete", [("project_id", PROJECT), ("owner_user_id", OWNER)])


@pytest.mark.parametrize("project_id", [OTHER_PROJECT, LEGACY_PROJECT, MISSING_PROJECT])
def test_regular_delete_no_match_returns_false(runtime, monkeypatch, project_id):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    assert runtime.repository.delete(project_id, owner_user_id=OWNER) is False
    assert runtime.client.rows == before
    assert_call(runtime, "delete", [("project_id", project_id), ("owner_user_id", OWNER)])


@pytest.mark.parametrize("project_id", [PROJECT, OTHER_PROJECT, LEGACY_PROJECT, MISSING_PROJECT])
def test_trusted_delete_filters_only_project(runtime, project_id):
    assert runtime.repository.delete(project_id, owner_user_id=None) is (project_id != MISSING_PROJECT)
    assert_call(runtime, "delete", [("project_id", project_id)])


@pytest.mark.parametrize("operation", ["get", "get_all", "update", "delete"])
def test_non_none_scope_is_never_treated_as_unscoped_even_when_empty(runtime, operation):
    if operation == "get_all":
        assert runtime.repository.get_all(owner_user_id="") == []
        filters = [("owner_user_id", "")]
    else:
        filters = [("project_id", PROJECT), ("owner_user_id", "")]
        if operation == "update":
            assert runtime.repository.update(PROJECT, FakeSchedule(PROJECT, "Denied"), owner_user_id="") is None
        elif operation == "delete":
            assert runtime.repository.delete(PROJECT, owner_user_id="") is False
        else:
            assert runtime.repository.get(PROJECT, owner_user_id="") is None
    assert_call(runtime, "select" if operation in {"get", "get_all"} else operation, filters)


@pytest.mark.parametrize("project_id", [PROJECT, OTHER_PROJECT, LEGACY_PROJECT, MISSING_PROJECT])
def test_admin_assignment_only_changes_owner_and_reports_matches(runtime, monkeypatch, project_id):
    before = deepcopy(runtime.client.rows)
    no_legacy_read_or_save(monkeypatch, runtime.repository)
    assert runtime.repository.assign_owner(project_id, OTHER_OWNER) is (project_id != MISSING_PROJECT)
    call = assert_call(runtime, "update", [("project_id", project_id)])
    assert call["payload"] == {"owner_user_id": OTHER_OWNER}
    if project_id != MISSING_PROJECT:
        before[project_id]["owner_user_id"] = OTHER_OWNER
    assert runtime.client.rows == before


def test_explicit_assignment_moves_visibility_between_owners(runtime):
    assert runtime.repository.assign_owner(PROJECT, OTHER_OWNER) is True
    assert runtime.repository.get(PROJECT, owner_user_id=OWNER) is None
    assert runtime.repository.get(PROJECT, owner_user_id=OTHER_OWNER).project_id == PROJECT
    assert [schedule.project_id for schedule in runtime.repository.get_all(owner_user_id=OWNER)] == []
    assert {schedule.project_id for schedule in runtime.repository.get_all(owner_user_id=OTHER_OWNER)} == {
        PROJECT, OTHER_PROJECT,
    }


@pytest.mark.parametrize("project_id", [PROJECT, OTHER_PROJECT, LEGACY_PROJECT, MISSING_PROJECT])
def test_legacy_service_save_excludes_owner_and_preserves_assignment(runtime, project_id):
    previous_owner = runtime.client.rows.get(project_id, {}).get("owner_user_id")
    schedule = FakeSchedule(project_id, "Service update")
    assert runtime.repository.save(schedule) is schedule
    call = assert_call(runtime, "upsert", [])
    assert call["on_conflict"] == "project_id"
    assert "owner_user_id" not in call["payload"]
    assert runtime.client.rows[project_id]["owner_user_id"] == previous_owner
    assert runtime.client.rows[project_id]["schedule"] == schedule.model_dump(mode="json")


def invoke_operation(runtime, operation):
    schedule = FakeSchedule(PROJECT, "Storage failure")
    if operation == "create":
        return runtime.repository.create(schedule, owner_user_id=OWNER)
    if operation == "get_all":
        return runtime.repository.get_all(owner_user_id=OWNER)
    if operation == "update":
        return runtime.repository.update(PROJECT, schedule, owner_user_id=OWNER)
    if operation == "assign_owner":
        return runtime.repository.assign_owner(PROJECT, OTHER_OWNER)
    if operation == "save":
        return runtime.repository.save(schedule)
    return getattr(runtime.repository, operation)(PROJECT, owner_user_id=OWNER)


@pytest.mark.parametrize("operation", ["get", "get_all", "create", "update", "delete", "assign_owner", "save", "exists"])
@pytest.mark.parametrize("code", ["PGRST204", "42703", "PGRST205", "42P01", "42501", "23503"])
def test_storage_schema_permission_and_foreign_key_errors_fail_closed(runtime, operation, code):
    before = deepcopy(runtime.client.rows)
    error = runtime.module.APIError({
        "message": "Synthetic storage failure", "code": code, "details": None, "hint": None,
    })
    runtime.client.failure = error
    expected = RuntimeError if code in {"PGRST204", "42703", "PGRST205", "42P01"} else runtime.module.APIError
    with pytest.raises(expected) as caught:
        invoke_operation(runtime, operation)
    if expected is RuntimeError:
        assert "backend/sql/project_ownership.sql" in str(caught.value)
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error
    assert runtime.client.rows == before
    assert len(runtime.client.calls) == 1  # No unscoped retry or fallback to unassigned storage.
    if operation in {"get", "get_all", "update", "delete", "exists"}:
        assert ("owner_user_id", OWNER) in runtime.client.calls[0]["filters"]


@pytest.mark.parametrize("operation", ["get", "get_all", "create", "update", "delete", "assign_owner", "save"])
def test_non_api_storage_failures_propagate_without_retry(runtime, operation):
    before = deepcopy(runtime.client.rows)
    error = TimeoutError("Synthetic offline timeout")
    runtime.client.failure = error
    with pytest.raises(TimeoutError) as caught:
        invoke_operation(runtime, operation)
    assert caught.value is error
    assert runtime.client.rows == before
    assert len(runtime.client.calls) == 1


@pytest.mark.parametrize("data", [None, []])
def test_create_requires_returned_insert_representation(runtime, data):
    runtime.client.response_data = data
    with pytest.raises(RuntimeError, match="did not return the created schedule"):
        runtime.repository.create(FakeSchedule(MISSING_PROJECT, "New"), owner_user_id=OWNER)
    assert_call(runtime, "insert", [])


@pytest.fixture
def migration_statements():
    sql = (BACKEND / "sql/project_ownership.sql").read_text(encoding="utf-8")
    assert "auth_accounts.sql" in sql and "schema.sql" in sql
    normalized = re.sub(r"--[^\n]*", "", sql).lower()
    return [" ".join(statement.split()) for statement in normalized.split(";") if statement.strip()]


def test_sql_is_only_one_time_additive_ownership_migration(migration_statements):
    assert migration_statements == [
        "begin",
        (
            "alter table public.project_schedules add column owner_user_id uuid "
            "references public.auth_accounts(user_id) on delete set null"
        ),
        "create index idx_project_schedules_owner_user_id on public.project_schedules (owner_user_id)",
        "alter table public.project_schedules enable row level security",
        "revoke all on table public.project_schedules from public, anon, authenticated",
        "grant select, insert, update, delete on table public.project_schedules to service_role",
        "commit",
    ]


def test_sql_leaves_existing_rows_unassigned_and_preserves_triggers(migration_statements):
    sql = " ".join(migration_statements)
    assert "not null" not in sql and "default" not in sql
    assert "if not exists" not in sql  # Deliberately one-time, not a silent schema repair.
    assert not re.search(r"\b(drop|truncate|trigger|function|policy)\b", sql)
    assert not re.search(r"\b(insert into|update public\.|delete from|alter owner)\b", sql)


def test_sql_denies_browser_access_and_grants_only_service_dml(migration_statements):
    assert [statement for statement in migration_statements if statement.startswith("revoke ")] == [
        "revoke all on table public.project_schedules from public, anon, authenticated",
    ]
    assert [statement for statement in migration_statements if statement.startswith("grant ")] == [
        "grant select, insert, update, delete on table public.project_schedules to service_role",
    ]
    assert "alter table public.project_schedules enable row level security" in migration_statements
