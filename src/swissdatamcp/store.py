"""Local DuckDB-backed cache and analytics helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import duckdb
import httpx
from slugify import slugify

from swissdatamcp.config import Settings
from swissdatamcp.models import AnalysisResult, TableReference


class StoreError(RuntimeError):
    """Raised when local storage or analysis fails."""


class DataStore:
    """Local DuckDB store for downloaded Swiss open-data resources."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Open a DuckDB connection."""

        return duckdb.connect(str(self.settings.database_path))

    async def download_resource(self, url: str, filename_hint: str | None = None) -> Path:
        """Download a public resource into the local cache."""

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise StoreError("Only http(s) resources are supported.")

        suffix = Path(parsed.path).suffix
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        stem = slugify(filename_hint or Path(parsed.path).stem or "resource")
        target = self.settings.downloads_dir / f"{stem}-{digest}{suffix or '.dat'}"

        if target.exists() and target.stat().st_size > 0:
            return target

        max_bytes = self.settings.max_download_mb * 1024 * 1024
        total = 0
        async with httpx.AsyncClient(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with target.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            target.unlink(missing_ok=True)
                            raise StoreError(
                                f"Download exceeds limit of {self.settings.max_download_mb} MB."
                            )
                        handle.write(chunk)

        return target

    def load_file_as_table(
        self,
        path: Path,
        dataset_id: str | None = None,
        resource_id: str | None = None,
        source_url: str | None = None,
        table_name: str | None = None,
        format_hint: str | None = None,
    ) -> TableReference:
        """Load a local CSV/JSON/Parquet file into DuckDB and return a table reference."""

        if not path.exists():
            raise StoreError(f"File does not exist: {path}")

        name = table_name or make_table_name(dataset_id or path.stem, resource_id or path.stem)
        suffix = path.suffix.lower()
        normalized_hint = (format_hint or "").lower().lstrip(".")
        escaped_path = str(path).replace("'", "''")

        if suffix in {".csv", ".tsv", ".txt"} or normalized_hint in {"csv", "tsv", "txt"}:
            read_expr = f"read_csv_auto('{escaped_path}', header=true, ignore_errors=true)"
        elif suffix in {".json", ".jsonl", ".ndjson"} or normalized_hint in {
            "json",
            "jsonl",
            "ndjson",
        }:
            read_expr = f"read_json_auto('{escaped_path}', ignore_errors=true)"
        elif suffix in {".parquet"} or normalized_hint == "parquet":
            read_expr = f"read_parquet('{escaped_path}')"
        else:
            raise StoreError(
                f"Unsupported file extension '{suffix}' with format hint '{format_hint}'. "
                "Supported: CSV, TSV, JSON, JSONL, Parquet."
            )

        with self.connect() as connection:
            connection.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM {read_expr}')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()]
            self._upsert_table_metadata(
                connection,
                name,
                dataset_id,
                resource_id,
                source_url,
                str(path),
                row_count,
                columns,
            )

        return TableReference(
            table_name=name,
            dataset_id=dataset_id,
            resource_id=resource_id,
            source_url=source_url,
            local_path=str(path),
            row_count=row_count,
            column_count=len(columns),
            columns=columns,
        )

    def inspect_table(self, table_name: str, sample_rows: int = 10) -> dict[str, Any]:
        """Return DuckDB schema and sample rows for a table."""

        with self.connect() as connection:
            ensure_table_exists(connection, table_name)
            schema_rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            columns = [
                {"name": row[1], "type": row[2], "nullable": not bool(row[3])}
                for row in schema_rows
            ]
            preview = connection.execute(
                f'SELECT * FROM "{table_name}" LIMIT ?', [max(1, min(sample_rows, 50))]
            ).fetchdf()
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        return {
            "table_name": table_name,
            "row_count": row_count,
            "columns": columns,
            "preview": dataframe_records(preview),
        }

    def list_tables(self) -> list[dict[str, Any]]:
        """Return locally loaded table metadata."""

        with self.connect() as connection:
            self._ensure_metadata_table(connection)
            rows = connection.execute(
                """
                SELECT table_name, dataset_id, resource_id, source_url, local_path, row_count, columns_json
                FROM swissdatamcp_tables
                ORDER BY loaded_at DESC
                """
            ).fetchall()
        return [
            {
                "table_name": row[0],
                "dataset_id": row[1],
                "resource_id": row[2],
                "source_url": row[3],
                "local_path": row[4],
                "row_count": row[5],
                "columns": json.loads(row[6] or "[]"),
            }
            for row in rows
        ]

    def query_table(
        self,
        table_name: str,
        select_columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Run a safe SELECT over a local table using simple equality/range filters."""

        limit = max(1, min(limit, 1000))
        with self.connect() as connection:
            ensure_table_exists(connection, table_name)
            available_columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            selected = select_columns or available_columns
            for column in selected:
                if column not in available_columns:
                    raise StoreError(f"Unknown column '{column}' for table '{table_name}'.")

            where_sql, params = build_where(filters or {}, available_columns)
            select_sql = ", ".join(f'"{column}"' for column in selected)
            query = f'SELECT {select_sql} FROM "{table_name}" {where_sql} LIMIT ?'
            df = connection.execute(query, [*params, limit]).fetchdf()
            row_count = len(df)
        return {
            "table_name": table_name,
            "columns": list(df.columns),
            "row_count": row_count,
            "rows": dataframe_records(df),
        }

    def calculate_change(
        self,
        table_name: str,
        group_column: str,
        time_column: str,
        value_column: str,
        start_value: Any,
        end_value: Any,
        filters: dict[str, Any] | None = None,
        sort_by: str = "percent_change",
        descending: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Calculate absolute and percent change between two time values by group."""

        if sort_by not in {"absolute_change", "percent_change", "start_value", "end_value"}:
            raise StoreError(
                "sort_by must be one of absolute_change, percent_change, start_value, end_value."
            )

        limit = max(1, min(limit, 1000))
        with self.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            for column in (group_column, time_column, value_column):
                if column not in columns:
                    raise StoreError(f"Unknown column '{column}' for table '{table_name}'.")

            where_sql, params = build_where(filters or {}, columns)
            extra_where = f" AND {where_sql[6:]}" if where_sql else ""
            order = "DESC" if descending else "ASC"
            df = connection.execute(
                f'''
                WITH scoped AS (
                  SELECT "{group_column}" AS group_value,
                         "{time_column}" AS time_value,
                         "{value_column}" AS metric_value
                  FROM "{table_name}"
                  WHERE "{time_column}" IN (?, ?){extra_where}
                ),
                pivoted AS (
                  SELECT group_value,
                         MAX(CASE WHEN time_value = ? THEN metric_value END) AS start_value,
                         MAX(CASE WHEN time_value = ? THEN metric_value END) AS end_value
                  FROM scoped
                  GROUP BY group_value
                )
                SELECT group_value,
                       start_value,
                       end_value,
                       end_value - start_value AS absolute_change,
                       CASE
                         WHEN start_value IS NULL OR start_value = 0 THEN NULL
                         ELSE ((end_value - start_value) * 100.0 / start_value)
                       END AS percent_change
                FROM pivoted
                WHERE start_value IS NOT NULL AND end_value IS NOT NULL
                ORDER BY "{sort_by}" {order} NULLS LAST
                LIMIT ?
                ''',
                [start_value, end_value, *params, start_value, end_value, limit],
            ).fetchdf()

        return {
            "table_name": table_name,
            "group_column": group_column,
            "time_column": time_column,
            "value_column": value_column,
            "start_value": start_value,
            "end_value": end_value,
            "sort_by": sort_by,
            "descending": descending,
            "rows": dataframe_records(df),
        }

    def analyze_table(
        self,
        table_name: str,
        group_by: str | None = None,
        value_column: str | None = None,
        limit_preview: int = 20,
    ) -> AnalysisResult:
        """Return generic summary stats and optional grouped numeric aggregation."""

        with self.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns_info = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            columns = [row[1] for row in columns_info]
            numeric_columns = [
                row[1]
                for row in columns_info
                if any(token in row[2].upper() for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL"))
            ]
            text_columns = [column for column in columns if column not in numeric_columns]
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            preview_df = connection.execute(
                f'SELECT * FROM "{table_name}" LIMIT ?', [max(1, min(limit_preview, 50))]
            ).fetchdf()

            summary: dict[str, Any] = {
                "numeric_summary": {},
                "grouped_summary": None,
            }
            for column in numeric_columns[:10]:
                stats = connection.execute(
                    f'''
                    SELECT
                      MIN("{column}") AS min_value,
                      MAX("{column}") AS max_value,
                      AVG("{column}") AS avg_value,
                      COUNT("{column}") AS non_null_count
                    FROM "{table_name}"
                    '''
                ).fetchone()
                summary["numeric_summary"][column] = {
                    "min": stats[0],
                    "max": stats[1],
                    "avg": stats[2],
                    "non_null_count": stats[3],
                }

            if group_by:
                if group_by not in columns:
                    raise StoreError(f"Unknown group_by column '{group_by}'.")
                metric = value_column or (numeric_columns[0] if numeric_columns else None)
                if metric:
                    if metric not in columns:
                        raise StoreError(f"Unknown value column '{metric}'.")
                    grouped_df = connection.execute(
                        f'''
                        SELECT "{group_by}" AS group_value,
                               COUNT(*) AS row_count,
                               AVG("{metric}") AS avg_value,
                               MIN("{metric}") AS min_value,
                               MAX("{metric}") AS max_value
                        FROM "{table_name}"
                        GROUP BY "{group_by}"
                        ORDER BY row_count DESC
                        LIMIT 50
                        '''
                    ).fetchdf()
                    summary["grouped_summary"] = {
                        "group_by": group_by,
                        "value_column": metric,
                        "rows": dataframe_records(grouped_df),
                    }

        return AnalysisResult(
            table_name=table_name,
            analysis_type="summary",
            row_count=row_count,
            column_count=len(columns),
            columns=columns,
            numeric_columns=numeric_columns,
            text_columns=text_columns,
            preview=dataframe_records(preview_df),
            summary=summary,
        )

    def _ensure_metadata_table(self, connection: duckdb.DuckDBPyConnection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS swissdatamcp_tables (
              table_name VARCHAR PRIMARY KEY,
              dataset_id VARCHAR,
              resource_id VARCHAR,
              source_url VARCHAR,
              local_path VARCHAR,
              row_count BIGINT,
              columns_json VARCHAR,
              loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def _upsert_table_metadata(
        self,
        connection: duckdb.DuckDBPyConnection,
        table_name: str,
        dataset_id: str | None,
        resource_id: str | None,
        source_url: str | None,
        local_path: str,
        row_count: int,
        columns: list[str],
    ) -> None:
        self._ensure_metadata_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO swissdatamcp_tables
            (table_name, dataset_id, resource_id, source_url, local_path, row_count, columns_json, loaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [table_name, dataset_id, resource_id, source_url, local_path, row_count, json.dumps(columns)],
        )


def make_table_name(dataset_id: str, resource_id: str) -> str:
    """Create a safe deterministic DuckDB table name."""

    base = slugify(f"{dataset_id}-{resource_id}", separator="_")
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    if not base or base[0].isdigit():
        base = f"t_{base}"
    return base[:60]


def ensure_table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> None:
    """Raise if a table is not present."""

    exists = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table_name]
    ).fetchone()[0]
    if not exists:
        raise StoreError(f"Unknown table '{table_name}'. Use list_local_tables first.")


def build_where(filters: dict[str, Any], available_columns: list[str]) -> tuple[str, list[Any]]:
    """Build a safe WHERE clause from simple filter syntax."""

    clauses: list[str] = []
    params: list[Any] = []
    for column, value in filters.items():
        if column not in available_columns:
            raise StoreError(f"Unknown filter column '{column}'.")
        if isinstance(value, dict):
            for operator, operand in value.items():
                if operator not in {
                    "eq",
                    "ne",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "contains",
                    "in",
                    "not_in",
                }:
                    raise StoreError(f"Unsupported filter operator '{operator}'.")
                sql_operator = {
                    "eq": "=",
                    "ne": "!=",
                    "gt": ">",
                    "gte": ">=",
                    "lt": "<",
                    "lte": "<=",
                }.get(operator)
                if operator == "contains":
                    clauses.append(f'CAST("{column}" AS VARCHAR) ILIKE ?')
                    params.append(f"%{operand}%")
                elif operator in {"in", "not_in"}:
                    if not isinstance(operand, list) or not operand:
                        raise StoreError(f"Filter operator '{operator}' requires a non-empty list.")
                    placeholders = ", ".join("?" for _ in operand)
                    not_sql = "NOT " if operator == "not_in" else ""
                    clauses.append(f'"{column}" {not_sql}IN ({placeholders})')
                    params.extend(operand)
                else:
                    clauses.append(f'"{column}" {sql_operator} ?')
                    params.append(operand)
        elif isinstance(value, list):
            if not value:
                raise StoreError(f"Filter list for '{column}' must not be empty.")
            placeholders = ", ".join("?" for _ in value)
            clauses.append(f'"{column}" IN ({placeholders})')
            params.extend(value)
        else:
            clauses.append(f'"{column}" = ?')
            params.append(value)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


def dataframe_records(df: Any) -> list[dict[str, Any]]:
    """Convert a dataframe to JSON-friendly records."""

    records = df.to_dict(orient="records")
    return json.loads(json.dumps(records, default=str))
