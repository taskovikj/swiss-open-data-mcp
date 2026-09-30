"""Chart generation for local SwissDataMCP artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from slugify import slugify  # noqa: E402

from swissdatamcp.config import Settings
from swissdatamcp.models import ChartResult
from swissdatamcp.store import (
    DataStore,
    StoreError,
    build_where,
    dataframe_records,
    ensure_table_exists,
    quote_identifier,
)

SUPPORTED_CHART_TYPES = {"line", "bar", "scatter"}


class ChartService:
    """Create local chart image artifacts from DuckDB tables."""

    def __init__(self, settings: Settings, store: DataStore | None = None):
        self.settings = settings
        self.store = store or DataStore(settings)

    def create_chart(
        self,
        table_name: str,
        x_column: str,
        y_columns: list[str],
        chart_type: str = "line",
        title: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 500,
    ) -> ChartResult:
        """Create a PNG chart from a local table."""

        if chart_type not in SUPPORTED_CHART_TYPES:
            raise StoreError(f"Unsupported chart_type '{chart_type}'. Use one of {sorted(SUPPORTED_CHART_TYPES)}.")
        if not y_columns:
            raise StoreError("At least one y_column is required.")

        limit = max(1, min(limit, 5000))
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            available_columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            for column in [x_column, *y_columns]:
                if column not in available_columns:
                    raise StoreError(f"Unknown column '{column}' for table '{table_name}'.")
            where_sql, params = build_where(filters or {}, available_columns)
            selected = ", ".join(quote_identifier(column) for column in [x_column, *y_columns])
            df = connection.execute(
                f'SELECT {selected} FROM "{table_name}" {where_sql} LIMIT ?',
                [*params, limit],
            ).fetchdf()

        if df.empty:
            raise StoreError("No rows available for chart after applying filters.")

        chart_title = title or f"{table_name}: {', '.join(y_columns)} by {x_column}"
        safe_name = slugify(chart_title)[:80] or "chart"
        chart_path = self.settings.outputs_dir / f"{safe_name}.png"

        fig, ax = plt.subplots(figsize=(11, 6))
        for y_column in y_columns:
            if chart_type == "line":
                ax.plot(df[x_column], df[y_column], marker="o", linewidth=1.8, label=y_column)
            elif chart_type == "bar":
                ax.bar(df[x_column].astype(str), df[y_column], label=y_column, alpha=0.75)
            elif chart_type == "scatter":
                ax.scatter(df[x_column], df[y_column], label=y_column)

        ax.set_title(chart_title)
        ax.set_xlabel(x_column)
        ax.set_ylabel(", ".join(y_columns))
        if len(y_columns) > 1:
            ax.legend()
        ax.grid(True, alpha=0.25)
        fig.autofmt_xdate(rotation=35)
        fig.tight_layout()
        fig.savefig(chart_path, dpi=160)
        plt.close(fig)

        return ChartResult(
            chart_path=str(chart_path),
            table_name=table_name,
            chart_type=chart_type,
            x_column=x_column,
            y_columns=y_columns,
            title=chart_title,
        )

    def preview_chart_data(
        self,
        table_name: str,
        x_column: str,
        y_columns: list[str],
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return chart-ready data without creating an image."""

        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            available_columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            for column in [x_column, *y_columns]:
                if column not in available_columns:
                    raise StoreError(f"Unknown column '{column}' for table '{table_name}'.")
            where_sql, params = build_where(filters or {}, available_columns)
            selected = ", ".join(quote_identifier(column) for column in [x_column, *y_columns])
            df = connection.execute(
                f'SELECT {selected} FROM "{table_name}" {where_sql} LIMIT ?',
                [*params, max(1, min(limit, 1000))],
            ).fetchdf()
        return {
            "table_name": table_name,
            "x_column": x_column,
            "y_columns": y_columns,
            "rows": dataframe_records(df),
        }


def absolute_path(path: str | Path) -> str:
    """Return a normalized absolute path string."""

    return str(Path(path).resolve())

