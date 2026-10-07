from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    MetaData,
    Table,
    and_,
    cast,
    func,
    inspect,
    or_,
    select,
    String,
)
from sqlalchemy.engine import Engine
from sqlalchemy import create_engine

from app.core.config import settings


@dataclass
class PostgreSQLKnowledge:
    """
    Represents project-plan information retrieved from PostgreSQL.
    """

    table_name: str
    record_id: str
    content: str
    metadata: dict[str, Any]


class PostgreSQLService:
    """
    Searches project-plan information stored by the Planning Agent
    in PostgreSQL.

    The exact Planning Agent database schema was not specified, so
    this service discovers tables and text-compatible columns rather
    than hard-coding a table name.

    project_id is used as the primary project-level filter whenever
    a matching project identifier column exists.
    """

    def __init__(self):
        self.engine: Engine = create_engine(
            settings.postgres_database_url,
            pool_pre_ping=True,
        )

    def _get_tables(self) -> list[Table]:
        """
        Discover PostgreSQL tables that are accessible to the
        application.
        """

        inspector = inspect(self.engine)

        table_names = inspector.get_table_names()

        metadata = MetaData()

        tables = []

        for table_name in table_names:
            try:
                table = Table(
                    table_name,
                    metadata,
                    autoload_with=self.engine,
                )

                tables.append(table)

            except Exception:
                # Ignore tables that cannot be reflected.
                continue

        return tables

    @staticmethod
    def _is_searchable_column(column) -> bool:
        """
        Determine whether a database column can reasonably be
        searched as text.
        """

        column_type = str(
            column.type
        ).lower()

        searchable_types = (
            "char",
            "text",
            "varchar",
            "string",
            "json",
            "jsonb",
        )

        return any(
            item in column_type
            for item in searchable_types
        )

    @staticmethod
    def _find_project_column(table: Table):
        """
        Find a likely project identifier column.
        """

        preferred_names = [
            "project_id",
            "projectid",
            "project",
        ]

        for preferred_name in preferred_names:
            for column in table.columns:
                if column.name.lower() == preferred_name:
                    return column

        return None

    @staticmethod
    def _find_record_id_column(table: Table):
        """
        Find a likely primary/key identifier column.
        """

        if table.primary_key.columns:
            return next(
                iter(table.primary_key.columns)
            )

        preferred_names = [
            "id",
            "plan_id",
            "task_id",
        ]

        for preferred_name in preferred_names:
            for column in table.columns:
                if column.name.lower() == preferred_name:
                    return column

        return None

    def search_project_knowledge(
        self,
        project_id: str,
        query: str,
        limit: int | None = None,
    ) -> list[PostgreSQLKnowledge]:
        """
        Search PostgreSQL project-plan information.

        The search checks textual columns for the supplied query
        and limits the search to the requested project whenever
        a project identifier column exists.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not query or not query.strip():
            raise ValueError(
                "query is required."
            )

        if limit is None:
            limit = settings.postgres_search_limit

        if limit <= 0:
            raise ValueError(
                "limit must be greater than zero."
            )

        query_pattern = f"%{query.strip()}%"

        results: list[PostgreSQLKnowledge] = []

        tables = self._get_tables()

        with self.engine.connect() as connection:

            for table in tables:

                searchable_columns = [
                    column
                    for column in table.columns
                    if self._is_searchable_column(column)
                ]

                if not searchable_columns:
                    continue

                project_column = (
                    self._find_project_column(table)
                )

                record_id_column = (
                    self._find_record_id_column(table)
                )

                search_conditions = []

                for column in searchable_columns:
                    search_conditions.append(
                        cast(
                            column,
                            String,
                        ).ilike(query_pattern)
                    )

                if not search_conditions:
                    continue

                conditions = [
                    or_(*search_conditions)
                ]

                if project_column is not None:
                    conditions.append(
                        cast(
                            project_column,
                            String,
                        ) == str(project_id)
                    )

                statement = (
                    select(table)
                    .where(and_(*conditions))
                    .limit(limit)
                )

                try:
                    rows = connection.execute(
                        statement
                    ).mappings().all()

                except Exception:
                    continue

                for row in rows:

                    row_dict = dict(row)

                    if record_id_column is not None:
                        record_value = row_dict.get(
                            record_id_column.name
                        )
                    else:
                        record_value = (
                            f"{table.name}-"
                            f"{len(results)}"
                        )

                    searchable_values = []

                    for column in searchable_columns:
                        value = row_dict.get(
                            column.name
                        )

                        if value is not None:
                            searchable_values.append(
                                f"{column.name}: {value}"
                            )

                    content = "\n".join(
                        searchable_values
                    )

                    results.append(
                        PostgreSQLKnowledge(
                            table_name=table.name,
                            record_id=str(
                                record_value
                            ),
                            content=content,
                            metadata={
                                "project_id": project_id,
                                "table": table.name,
                                "record_id": str(
                                    record_value
                                ),
                                "knowledge_type": "planning_agent",
                            },
                        )
                    )

                    if len(results) >= limit:
                        return results

        return results

    def get_project_records(
        self,
        project_id: str,
        limit: int | None = None,
    ) -> list[PostgreSQLKnowledge]:
        """
        Retrieve project-related planning information from
        PostgreSQL without requiring a specific search term.

        This is useful when the Risk Agent needs the latest plan
        information as context.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if limit is None:
            limit = settings.postgres_search_limit

        results: list[PostgreSQLKnowledge] = []

        tables = self._get_tables()

        with self.engine.connect() as connection:

            for table in tables:

                project_column = (
                    self._find_project_column(table)
                )

                if project_column is None:
                    continue

                record_id_column = (
                    self._find_record_id_column(table)
                )

                statement = (
                    select(table)
                    .where(
                        cast(
                            project_column,
                            String,
                        ) == str(project_id)
                    )
                    .limit(limit)
                )

                try:
                    rows = connection.execute(
                        statement
                    ).mappings().all()

                except Exception:
                    continue

                searchable_columns = [
                    column
                    for column in table.columns
                    if self._is_searchable_column(column)
                ]

                for row in rows:

                    row_dict = dict(row)

                    if record_id_column is not None:
                        record_value = row_dict.get(
                            record_id_column.name
                        )
                    else:
                        record_value = (
                            f"{table.name}-"
                            f"{len(results)}"
                        )

                    content_parts = []

                    for column in searchable_columns:
                        value = row_dict.get(
                            column.name
                        )

                        if value is not None:
                            content_parts.append(
                                f"{column.name}: {value}"
                            )

                    content = "\n".join(
                        content_parts
                    )

                    results.append(
                        PostgreSQLKnowledge(
                            table_name=table.name,
                            record_id=str(
                                record_value
                            ),
                            content=content,
                            metadata={
                                "project_id": project_id,
                                "table": table.name,
                                "record_id": str(
                                    record_value
                                ),
                                "knowledge_type": "planning_agent",
                            },
                        )
                    )

                    if len(results) >= limit:
                        return results

        return results