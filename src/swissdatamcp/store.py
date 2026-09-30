"""Local DuckDB-backed cache and analytics helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import duckdb
import httpx
from slugify import slugify

from swissdatamcp.config import Settings
from swissdatamcp.models import AnalysisResult, TableReference
from swissdatamcp.network import validate_public_url


class StoreError(RuntimeError):
    """Raised when local storage or analysis fails."""


class DataStore:
    """Local DuckDB store for downloaded Swiss open-data resources."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = RLock()

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Serialize database access within this server process."""

        with self._lock, duckdb.connect(str(self.settings.database_path)) as connection:
            yield connection

    async def download_resource(
        self, url: str, filename_hint: str | None = None, refresh: bool = False
    ) -> Path:
        """Download a public resource into the local cache."""

        try:
            await asyncio.wait_for(validate_public_url(url), self.settings.request_timeout_seconds)
        except (ValueError, TimeoutError) as exc:
            raise StoreError(str(exc)) from exc
        parsed = urlparse(url)

        suffix = Path(parsed.path).suffix
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        stem = slugify(filename_hint or Path(parsed.path).stem or "resource")[:48] or "resource"
        target = self.settings.downloads_dir / f"{stem}-{digest}{suffix or '.dat'}"
        receipt = target.with_suffix(target.suffix + ".sha256")

        max_bytes = self.settings.max_download_mb * 1024 * 1024
        if (
            not refresh
            and target.exists()
            and receipt.exists()
            and 0 < target.stat().st_size <= max_bytes
        ):
            with target.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual_hash == receipt.read_text(encoding="ascii").strip():
                return target

        temporary: Path | None = None
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": self.settings.user_agent},
                timeout=self.settings.request_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                current_url = url
                for _ in range(6):
                    async with client.stream("GET", current_url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise StoreError("Resource redirect has no Location header.")
                            next_url = str(response.url.join(location))
                            await asyncio.wait_for(
                                validate_public_url(next_url), self.settings.request_timeout_seconds
                            )
                            if (
                                urlparse(current_url).scheme == "https"
                                and urlparse(next_url).scheme != "https"
                            ):
                                raise StoreError(
                                    "Resource redirect cannot downgrade HTTPS to HTTP."
                                )
                            current_url = next_url
                            continue
                        response.raise_for_status()
                        length = response.headers.get("content-length")
                        if length and int(length) > max_bytes:
                            raise StoreError("Download exceeds configured size limit.")
                        total = 0
                        with NamedTemporaryFile(
                            mode="wb", dir=self.settings.downloads_dir, suffix=".part", delete=False
                        ) as handle:
                            temporary = Path(handle.name)
                            async for chunk in response.aiter_bytes():
                                total += len(chunk)
                                if total > max_bytes:
                                    raise StoreError("Download exceeds configured size limit.")
                                handle.write(chunk)
                        if total == 0:
                            raise StoreError("Resource returned an empty download.")
                        with temporary.open("rb") as handle:
                            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
                        temporary.replace(target)
                        receipt.write_text(checksum, encoding="ascii")
                        return target
                raise StoreError("Resource exceeded the redirect limit.")
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            raise StoreError(f"Resource download failed: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_file_as_table(
        self,
        path: Path,
        dataset_id: str | None = None,
        resource_id: str | None = None,
        source_url: str | None = None,
        table_name: str | None = None,
        format_hint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TableReference:
        """Load a local CSV/JSON/Parquet file into DuckDB and return a table reference."""

        if not path.exists():
            raise StoreError(f"File does not exist: {path}")

        name = table_name or make_table_name(dataset_id or path.stem, resource_id or path.stem)
        validate_table_name(name)
        suffix = path.suffix.lower()
        normalized_hint = (format_hint or "").lower().lstrip(".")
        escaped_path = str(path).replace("'", "''")

        if suffix in {".csv", ".tsv", ".txt"} or normalized_hint in {"csv", "tsv", "txt"}:
            read_expr = f"read_csv_auto('{escaped_path}', header=true)"
        elif suffix in {".json", ".jsonl", ".ndjson"} or normalized_hint in {
            "json",
            "jsonl",
            "ndjson",
        }:
            read_expr = f"read_json_auto('{escaped_path}')"
        elif suffix in {".parquet"} or normalized_hint == "parquet":
            read_expr = f"read_parquet('{escaped_path}')"
        else:
            raise StoreError(
                f"Unsupported file extension '{suffix}' with format hint '{format_hint}'. "
                "Supported: CSV, TSV, JSON, JSONL, Parquet."
            )

        with path.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        metadata = {
            **(metadata or {}),
            "sha256": checksum,
        }
        with self.connect() as connection:
            connection.begin()
            connection.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM {read_expr}')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()
            ]
            self._upsert_table_metadata(
                connection,
                name,
                dataset_id,
                resource_id,
                source_url,
                str(path),
                row_count,
                columns,
                metadata,
            )
            connection.commit()

        return TableReference(
            table_name=name,
            dataset_id=dataset_id,
            resource_id=resource_id,
            source_url=source_url,
            local_path=str(path),
            row_count=row_count,
            column_count=len(columns),
            columns=columns,
            metadata=metadata or {},
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
                SELECT table_name,
                       dataset_id,
                       resource_id,
                       source_url,
                       local_path,
                       row_count,
                       columns_json,
                       loaded_at,
                       metadata_json
                FROM swissdatamcp_tables
                ORDER BY loaded_at DESC
                """
            ).fetchall()
        tables = []
        for row in rows:
            metadata = parse_metadata(row[8])
            loaded_at = row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7])
            tables.append(
                {
                    "table_name": row[0],
                    "dataset_id": row[1],
                    "resource_id": row[2],
                    "source_url": row[3],
                    "local_path": row[4],
                    "row_count": row[5],
                    "columns": json.loads(row[6] or "[]"),
                    "loaded_at": loaded_at,
                    "accessed_at": metadata.get("accessed_at") or loaded_at,
                    "metadata": metadata,
                }
            )
        return tables

    def query_table(
        self,
        table_name: str,
        select_columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Run a safe SELECT over a local table using simple equality/range filters."""

        limit = max(1, min(limit, 1000))
        if offset < 0:
            raise StoreError("offset must be non-negative.")
        with self.connect() as connection:
            ensure_table_exists(connection, table_name)
            available_columns = [
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            selected = select_columns or available_columns
            for column in selected:
                if column not in available_columns:
                    raise StoreError(f"Unknown column '{column}' for table '{table_name}'.")

            where_sql, params = build_where(filters or {}, available_columns)
            select_sql = ", ".join(quote_identifier(column) for column in selected)
            query = f'SELECT {select_sql} FROM "{table_name}" {where_sql} LIMIT ? OFFSET ?'
            df = connection.execute(query, [*params, limit + 1, offset]).fetchdf()
            has_more = len(df) > limit
            df = df.head(limit)
            row_count = len(df)
        return {
            "table_name": table_name,
            "columns": list(df.columns),
            "row_count": row_count,
            "rows": dataframe_records(df),
            "offset": offset,
            "has_more": has_more,
            "next_offset": offset + row_count if has_more else None,
        }

    def export_table(
        self,
        table_name: str,
        format: str = "csv",
        filters: dict[str, Any] | None = None,
        max_rows: int = 100000,
    ) -> dict[str, Any]:
        """Export a bounded table snapshot with a hash and provenance manifest."""

        options = {
            "csv": "FORMAT CSV, HEADER TRUE",
            "json": "FORMAT JSON, ARRAY TRUE",
            "parquet": "FORMAT PARQUET",
        }
        if format not in options:
            raise StoreError("format must be csv, json, or parquet.")
        if not 1 <= max_rows <= 1000000:
            raise StoreError("max_rows must be between 1 and 1000000.")
        export_id = uuid.uuid4().hex
        target = self.settings.outputs_dir / f"export-{export_id}.{format}"
        temporary = target.with_suffix(".part")
        escaped_path = str(temporary).replace("'", "''")
        try:
            with self.connect() as connection:
                ensure_table_exists(connection, table_name)
                columns = [
                    row[1]
                    for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                ]
                where, params = build_where(filters or {}, columns)
                total = connection.execute(
                    f'SELECT COUNT(*) FROM "{table_name}" {where}', params
                ).fetchone()[0]
                connection.execute(
                    f"COPY (SELECT * FROM {quote_identifier(table_name)} {where} LIMIT {max_rows}) "
                    f"TO '{escaped_path}' ({options[format]})",
                    params,
                )
                source = next(
                    (table for table in self.list_tables() if table["table_name"] == table_name), {}
                )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        with target.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        manifest = {
            "export_id": export_id,
            "table_name": table_name,
            "format": format,
            "path": str(target),
            "row_count": min(total, max_rows),
            "total_matching_rows": total,
            "truncated": total > max_rows,
            "filters": filters or {},
            "sha256": checksum,
            "created_at": datetime.now(UTC).isoformat(),
            "source": source,
            "resource_uri": f"swissdatamcp://export/{export_id}",
        }
        (self.settings.outputs_dir / f"export-{export_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return manifest

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
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
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
                  SELECT {quote_identifier(group_column)} AS group_value,
                         {quote_identifier(time_column)} AS time_value,
                         {quote_identifier(value_column)} AS metric_value
                  FROM "{table_name}"
                  WHERE {quote_identifier(time_column)} IN (?, ?){extra_where}
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
                if any(
                    token in row[2].upper()
                    for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL")
                )
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
                      MIN({quote_identifier(column)}) AS min_value,
                      MAX({quote_identifier(column)}) AS max_value,
                      AVG({quote_identifier(column)}) AS avg_value,
                      COUNT({quote_identifier(column)}) AS non_null_count
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
                        SELECT {quote_identifier(group_by)} AS group_value,
                               COUNT(*) AS row_count,
                               AVG({quote_identifier(metric)}) AS avg_value,
                               MIN({quote_identifier(metric)}) AS min_value,
                               MAX({quote_identifier(metric)}) AS max_value
                        FROM "{table_name}"
                        GROUP BY {quote_identifier(group_by)}
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
              metadata_json VARCHAR,
              loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing = {
            row[1]
            for row in connection.execute("PRAGMA table_info('swissdatamcp_tables')").fetchall()
        }
        if "metadata_json" not in existing:
            connection.execute("ALTER TABLE swissdatamcp_tables ADD COLUMN metadata_json VARCHAR")

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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_metadata_table(connection)
        metadata_payload = {
            **(metadata or {}),
            "accessed_at": (metadata or {}).get("accessed_at")
            or datetime.now(UTC).isoformat(timespec="seconds"),
        }
        connection.execute(
            """
            INSERT OR REPLACE INTO swissdatamcp_tables
            (
              table_name,
              dataset_id,
              resource_id,
              source_url,
              local_path,
              row_count,
              columns_json,
              metadata_json,
              loaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                table_name,
                dataset_id,
                resource_id,
                source_url,
                local_path,
                row_count,
                json.dumps(columns),
                json.dumps(metadata_payload, ensure_ascii=False, default=str),
            ],
        )


def make_table_name(dataset_id: str, resource_id: str) -> str:
    """Create a safe deterministic DuckDB table name."""

    base = slugify(f"{dataset_id}-{resource_id}", separator="_")
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    if not base or base[0].isdigit():
        base = f"t_{base}"
    return base[:60]


def parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    """Parse table metadata JSON, returning an empty dict on old rows."""

    if not metadata_json:
        return {}
    try:
        data = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def ensure_table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> None:
    """Raise if a table is not present."""

    validate_table_name(table_name)
    exists = connection.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table_name]
    ).fetchone()[0]
    if not exists:
        raise StoreError(f"Unknown table '{table_name}'. Use list_local_tables first.")


def validate_table_name(name: str) -> None:
    """Keep caller-controlled names out of SQL syntax and internal metadata."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
        raise StoreError(
            "Table names must use letters, digits, underscores and start with a letter."
        )
    if name.lower().startswith("swissdatamcp_"):
        raise StoreError("The swissdatamcp_ table prefix is reserved for internal metadata.")


def quote_identifier(value: str) -> str:
    """Escape a source column name, including embedded double quotes."""

    return '"' + value.replace('"', '""') + '"'


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
                    clauses.append(f"CAST({quote_identifier(column)} AS VARCHAR) ILIKE ?")
                    params.append(f"%{operand}%")
                elif operator in {"in", "not_in"}:
                    if not isinstance(operand, list) or not operand:
                        raise StoreError(f"Filter operator '{operator}' requires a non-empty list.")
                    placeholders = ", ".join("?" for _ in operand)
                    not_sql = "NOT " if operator == "not_in" else ""
                    clauses.append(f"{quote_identifier(column)} {not_sql}IN ({placeholders})")
                    params.extend(operand)
                else:
                    if operand is None and operator in {"eq", "ne"}:
                        not_sql = "NOT " if operator == "ne" else ""
                        clauses.append(f"{quote_identifier(column)} IS {not_sql}NULL")
                        continue
                    clauses.append(f"{quote_identifier(column)} {sql_operator} ?")
                    params.append(operand)
        elif isinstance(value, list):
            if not value:
                raise StoreError(f"Filter list for '{column}' must not be empty.")
            placeholders = ", ".join("?" for _ in value)
            clauses.append(f"{quote_identifier(column)} IN ({placeholders})")
            params.extend(value)
        else:
            if value is None:
                clauses.append(f"{quote_identifier(column)} IS NULL")
                continue
            clauses.append(f"{quote_identifier(column)} = ?")
            params.append(value)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


def dataframe_records(df: Any) -> list[dict[str, Any]]:
    """Convert a dataframe to JSON-friendly records."""

    return json.loads(df.to_json(orient="records", date_format="iso", double_precision=15))
