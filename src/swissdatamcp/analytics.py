"""Reusable analytics helpers for SwissDataMCP MCP tools."""

from __future__ import annotations

import json
import math
import re
import shutil
import zipfile
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from slugify import slugify

from swissdatamcp.config import Settings
from swissdatamcp.labels import CANTON_BY_CODE, normalize_label_key, readable_column_label
from swissdatamcp.sessions import SessionService
from swissdatamcp.store import DataStore, build_where, dataframe_records, ensure_table_exists


class AnalyticsError(RuntimeError):
    """Raised when a higher-level analytics operation cannot be completed."""


class AnalyticsService:
    """Higher-level profiling, charting, and statistical helpers."""

    def __init__(self, settings: Settings, store: DataStore, sessions: SessionService):
        self.settings = settings
        self.store = store
        self.sessions = sessions

    def detect_schema_semantics(self, table_name: str) -> dict[str, Any]:
        """Infer common semantic roles from local table column names and types."""

        schema = self.store.inspect_table(table_name, sample_rows=3)
        columns = schema["columns"]
        roles: dict[str, list[str]] = {
            "time": [],
            "canton": [],
            "municipality": [],
            "latitude": [],
            "longitude": [],
            "easting": [],
            "northing": [],
            "metric": [],
            "category": [],
            "identifier": [],
            "url": [],
            "source": [],
        }
        for column in columns:
            name = column["name"]
            lower = normalize_name(name)
            col_type = column["type"].upper()
            if any(token in lower for token in ("year", "jahr", "annee", "anno", "date", "datum", "time", "zeit")):
                roles["time"].append(name)
            if lower in {"canton", "kanton", "kt", "canton_code"} or "canton" in lower or "kanton" in lower:
                roles["canton"].append(name)
            if any(token in lower for token in ("gemeinde", "municipality", "commune", "ortschaft")):
                roles["municipality"].append(name)
            if lower in {"lat", "latitude", "y_wgs84"} or "latitude" in lower:
                roles["latitude"].append(name)
            if lower in {"lon", "lng", "longitude", "x_wgs84"} or "longitude" in lower:
                roles["longitude"].append(name)
            if lower in {"coord_e_ch", "easting", "lv95_e"} or "chlv95_e" in lower:
                roles["easting"].append(name)
            if lower in {"coord_n_ch", "northing", "lv95_n"} or "chlv95_n" in lower:
                roles["northing"].append(name)
            if (
                lower in {"id", "uid", "uuid", "identifier"}
                or lower.endswith("_id")
                or lower.endswith("uid")
                or "ark" in lower
            ):
                roles["identifier"].append(name)
            if "url" in lower or "uri" in lower or "link" in lower:
                roles["url"].append(name)
            if "source" in lower or "quelle" in lower:
                roles["source"].append(name)
            if is_numeric_type(col_type):
                roles["metric"].append(name)
            elif name not in roles["url"] and name not in roles["identifier"]:
                roles["category"].append(name)

        primary = {
            "time_column": first_or_none(roles["time"]),
            "canton_column": first_or_none(roles["canton"]),
            "municipality_column": first_or_none(roles["municipality"]),
            "latitude_column": first_or_none(roles["latitude"]),
            "longitude_column": first_or_none(roles["longitude"]),
            "easting_column": first_or_none(roles["easting"]),
            "northing_column": first_or_none(roles["northing"]),
            "metric_column": first_or_none([col for col in roles["metric"] if col not in roles["time"]]),
            "category_column": first_or_none(roles["category"]),
        }
        return {
            "table_name": table_name,
            "row_count": schema["row_count"],
            "roles": roles,
            "primary": primary,
            "coordinate_pair_detected": bool(primary["latitude_column"] and primary["longitude_column"]),
            "projected_coordinate_pair_detected": bool(
                primary["easting_column"] and primary["northing_column"]
            ),
        }

    def profile_dataset(
        self,
        table_name: str,
        max_columns: int = 80,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """Return row counts, missingness, ranges, categories, and coordinates."""

        semantics = self.detect_schema_semantics(table_name)
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            schema_rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            column_profiles: list[dict[str, Any]] = []
            top_categories: dict[str, list[dict[str, Any]]] = {}
            numeric_ranges: dict[str, dict[str, Any]] = {}
            date_ranges: dict[str, dict[str, Any]] = {}

            for row in schema_rows[: max(1, max_columns)]:
                name = row[1]
                col_type = row[2]
                quoted = quote_identifier(name)
                missing = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM "{table_name}"
                    WHERE {quoted} IS NULL OR TRIM(CAST({quoted} AS VARCHAR)) = ''
                    """
                ).fetchone()[0]
                distinct = connection.execute(
                    f'SELECT COUNT(DISTINCT {quoted}) FROM "{table_name}" WHERE {quoted} IS NOT NULL'
                ).fetchone()[0]
                profile = {
                    "name": name,
                    "type": col_type,
                    "missing_count": missing,
                    "missing_percent": round((missing * 100.0 / row_count), 2) if row_count else 0.0,
                    "distinct_count": distinct,
                    "semantic_roles": roles_for_column(semantics["roles"], name),
                }
                if is_numeric_type(col_type):
                    stats = connection.execute(
                        f"""
                        SELECT MIN({quoted}), MAX({quoted}), AVG({quoted})
                        FROM "{table_name}"
                        WHERE {quoted} IS NOT NULL
                        """
                    ).fetchone()
                    profile["numeric"] = {
                        "min": stats[0],
                        "max": stats[1],
                        "avg": stats[2],
                    }
                    numeric_ranges[name] = profile["numeric"]
                    if name in semantics["roles"]["time"]:
                        date_ranges[name] = {"min": stats[0], "max": stats[1], "kind": "numeric_time"}
                if should_collect_categories(col_type, distinct, row_count):
                    top_df = connection.execute(
                        f"""
                        SELECT CAST({quoted} AS VARCHAR) AS value, COUNT(*) AS count
                        FROM "{table_name}"
                        WHERE {quoted} IS NOT NULL AND TRIM(CAST({quoted} AS VARCHAR)) != ''
                        GROUP BY 1
                        ORDER BY count DESC, value
                        LIMIT ?
                        """,
                        [max(1, min(top_k, 50))],
                    ).fetchdf()
                    top_categories[name] = dataframe_records(top_df)
                    profile["top_values"] = top_categories[name]
                column_profiles.append(profile)

            coordinate_summary = None
            lat_col = semantics["primary"]["latitude_column"]
            lon_col = semantics["primary"]["longitude_column"]
            if lat_col and lon_col:
                lat = quote_identifier(lat_col)
                lon = quote_identifier(lon_col)
                coord = connection.execute(
                    f"""
                    SELECT COUNT(*) AS coordinate_rows,
                           MIN({lat}) AS min_lat,
                           MAX({lat}) AS max_lat,
                           MIN({lon}) AS min_lon,
                           MAX({lon}) AS max_lon
                    FROM "{table_name}"
                    WHERE {lat} IS NOT NULL AND {lon} IS NOT NULL
                    """
                ).fetchone()
                coordinate_summary = {
                    "latitude_column": lat_col,
                    "longitude_column": lon_col,
                    "coordinate_rows": coord[0],
                    "bbox": {
                        "min_lat": coord[1],
                        "max_lat": coord[2],
                        "min_lon": coord[3],
                        "max_lon": coord[4],
                    },
                }

        return {
            "table_name": table_name,
            "row_count": row_count,
            "column_count": len(schema_rows),
            "columns_profiled": len(column_profiles),
            "semantics": semantics,
            "columns": column_profiles,
            "numeric_ranges": numeric_ranges,
            "date_ranges": date_ranges,
            "top_categories": top_categories,
            "coordinate_summary": coordinate_summary,
        }

    def clean_table_for_analysis(
        self,
        table_name: str,
        output_table_name: str | None = None,
        trim_text: bool = True,
    ) -> dict[str, Any]:
        """Create a normalized copy of a table with safe snake_case column names."""

        output = output_table_name or f"{normalize_identifier(table_name)}_clean"
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            schema_rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            used: set[str] = set()
            column_map: dict[str, str] = {}
            select_parts: list[str] = []
            for row in schema_rows:
                original = row[1]
                col_type = row[2].upper()
                clean = unique_name(normalize_identifier(original), used)
                column_map[original] = clean
                quoted_original = quote_identifier(original)
                quoted_clean = quote_identifier(clean)
                if trim_text and not is_numeric_type(col_type):
                    select_parts.append(
                        f"NULLIF(TRIM(CAST({quoted_original} AS VARCHAR)), '') AS {quoted_clean}"
                    )
                else:
                    select_parts.append(f"{quoted_original} AS {quoted_clean}")

            connection.execute(
                f'CREATE OR REPLACE TABLE "{output}" AS SELECT {", ".join(select_parts)} FROM "{table_name}"'
            )
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"clean:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns,
            )

        return {
            "source_table": table_name,
            "output_table": output,
            "row_count": row_count,
            "column_count": len(columns),
            "column_map": column_map,
        }

    def recommend_charts_for_table(
        self,
        table_name: str,
        question: str | None = None,
        max_recommendations: int = 8,
    ) -> dict[str, Any]:
        """Suggest useful charts and analysis steps for a loaded table."""

        profile = self.profile_dataset(table_name)
        semantics = profile["semantics"]
        primary = semantics["primary"]
        numeric_columns = [
            column["name"]
            for column in profile["columns"]
            if "metric" in column.get("semantic_roles", []) and column["name"] not in semantics["roles"]["time"]
        ]
        category_columns = choose_category_columns(profile, max_columns=4)
        recommendations: list[dict[str, Any]] = []

        if primary["longitude_column"] and primary["latitude_column"]:
            recommendations.append(
                {
                    "kind": "map",
                    "title": "Map of records",
                    "chart_type": "map",
                    "table_name": table_name,
                    "x_column": primary["longitude_column"],
                    "y_column": primary["latitude_column"],
                    "series_column": primary["category_column"],
                    "why": "The table has longitude and latitude columns.",
                    "next_tool": "add_chart_to_session",
                }
            )

        if primary["time_column"]:
            metric = primary["metric_column"]
            recommendations.append(
                {
                    "kind": "time_series",
                    "title": f"{readable_column_label(metric or 'record_count')} over time",
                    "chart_type": "line",
                    "source_table": table_name,
                    "time_column": primary["time_column"],
                    "value_column": metric,
                    "aggregation": "sum" if metric else "count",
                    "why": "The table has a detected time column.",
                    "next_tool": "time_series_analysis",
                }
            )

        for category in category_columns:
            recommendations.append(
                {
                    "kind": "category_bar",
                    "title": f"Top {readable_column_label(category)}",
                    "chart_type": "bar",
                    "source_table": table_name,
                    "category_column": category,
                    "why": "This categorical column has a manageable number of values.",
                    "next_tool": "create_dashboard_from_question",
                }
            )

        if len(numeric_columns) >= 2:
            recommendations.append(
                {
                    "kind": "scatter",
                    "title": f"{readable_column_label(numeric_columns[0])} vs {readable_column_label(numeric_columns[1])}",
                    "chart_type": "scatter",
                    "table_name": table_name,
                    "x_column": numeric_columns[0],
                    "y_column": numeric_columns[1],
                    "series_column": primary["category_column"],
                    "why": "The table has at least two numeric columns.",
                    "next_tool": "correlation_analysis",
                }
            )

        if len(numeric_columns) >= 3:
            recommendations.append(
                {
                    "kind": "correlation_matrix",
                    "title": "Correlation matrix",
                    "chart_type": "heatmap",
                    "table_name": table_name,
                    "numeric_columns": numeric_columns[:12],
                    "why": "The table has enough numeric columns to compare pairwise relationships.",
                    "next_tool": "correlation_matrix_analysis",
                }
            )

        return {
            "table_name": table_name,
            "question": question,
            "row_count": profile["row_count"],
            "detected_semantics": primary,
            "recommendations": recommendations[: max(1, min(max_recommendations, 20))],
            "caveats": [
                "Use aggregation before charting repeated time/category rows.",
                "Normalize rates before comparing cantons with very different population sizes.",
                "Treat correlations as descriptive, not causal.",
            ],
        }

    def suggest_join_keys(
        self,
        left_table: str,
        right_table: str,
        max_suggestions: int = 10,
    ) -> dict[str, Any]:
        """Suggest likely join columns between two local tables."""

        left_profile = self.profile_dataset(left_table, max_columns=80, top_k=5)
        right_profile = self.profile_dataset(right_table, max_columns=80, top_k=5)
        suggestions: list[dict[str, Any]] = []
        for left_column in left_profile["columns"]:
            for right_column in right_profile["columns"]:
                score, reasons = self._join_key_score(
                    left_table,
                    right_table,
                    left_column,
                    right_column,
                    left_profile["semantics"]["roles"],
                    right_profile["semantics"]["roles"],
                )
                if score > 0:
                    suggestions.append(
                        {
                            "left_column": left_column["name"],
                            "right_column": right_column["name"],
                            "score": round(score, 3),
                            "reasons": reasons,
                        }
                    )
        suggestions.sort(key=lambda item: item["score"], reverse=True)
        return {
            "left_table": left_table,
            "right_table": right_table,
            "suggestions": suggestions[: max(1, min(max_suggestions, 50))],
        }

    def compare_table_granularity(
        self,
        left_table: str,
        right_table: str | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Describe row grain and distinct counts for one or two tables."""

        left = self._granularity_summary(left_table, dimensions)
        right = self._granularity_summary(right_table, dimensions) if right_table else None
        comparison = None
        if right:
            left_roles = left["semantics"]["primary"]
            right_roles = right["semantics"]["primary"]
            comparison = {
                "shared_detected_roles": [
                    role
                    for role in ("time_column", "canton_column", "municipality_column")
                    if left_roles.get(role) and right_roles.get(role)
                ],
                "row_count_ratio": round(left["row_count"] / right["row_count"], 6)
                if right["row_count"]
                else None,
                "caveat": "Matching row counts do not guarantee the same grain; inspect distinct dimension combinations.",
            }
        return {"left": left, "right": right, "comparison": comparison}

    def can_correlate_tables(
        self,
        left_table: str,
        right_table: str,
        left_join_column: str | None = None,
        right_join_column: str | None = None,
        left_value_column: str | None = None,
        right_value_column: str | None = None,
    ) -> dict[str, Any]:
        """Check whether two tables are ready for a joined correlation."""

        join_suggestions = self.suggest_join_keys(left_table, right_table, max_suggestions=5)
        best_join = join_suggestions["suggestions"][0] if join_suggestions["suggestions"] else None
        left_profile = self.profile_dataset(left_table, max_columns=80, top_k=5)
        right_profile = self.profile_dataset(right_table, max_columns=80, top_k=5)
        left_join = left_join_column or (best_join or {}).get("left_column")
        right_join = right_join_column or (best_join or {}).get("right_column")
        left_metric = left_value_column or left_profile["semantics"]["primary"]["metric_column"]
        right_metric = right_value_column or right_profile["semantics"]["primary"]["metric_column"]
        issues: list[str] = []
        if not left_join or not right_join:
            issues.append("No join key was detected. Pass join columns explicitly.")
        if not left_metric:
            issues.append("No numeric metric was detected in the left table.")
        if not right_metric:
            issues.append("No numeric metric was detected in the right table.")

        join_rows = None
        if not issues:
            with self.store.connect() as connection:
                for table in (left_table, right_table):
                    ensure_table_exists(connection, table)
                join_rows = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM "{left_table}" l
                    INNER JOIN "{right_table}" r
                      ON CAST(l.{quote_identifier(left_join)} AS VARCHAR)
                       = CAST(r.{quote_identifier(right_join)} AS VARCHAR)
                    WHERE l.{quote_identifier(left_metric)} IS NOT NULL
                      AND r.{quote_identifier(right_metric)} IS NOT NULL
                    """,
                ).fetchone()[0]
            if join_rows < 2:
                issues.append("The selected join and metric columns produce fewer than two complete rows.")

        return {
            "left_table": left_table,
            "right_table": right_table,
            "can_correlate": not issues,
            "join_columns": {"left": left_join, "right": right_join},
            "value_columns": {"left": left_metric, "right": right_metric},
            "join_rows": join_rows,
            "join_suggestions": join_suggestions["suggestions"],
            "issues": issues,
            "recommended_next_tool": "compare_datasets" if not issues else None,
            "caveats": [
                "Check whether the two metrics refer to the same time period.",
                "Use per-capita or rate normalization when comparing canton-level totals.",
            ],
        }

    def normalize_canton_codes(
        self,
        table_name: str,
        canton_column: str | None = None,
        output_table_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a derived table with normalized canton code, abbreviation, and name columns."""

        semantics = self.detect_schema_semantics(table_name)
        column = canton_column or semantics["primary"]["canton_column"]
        if not column:
            raise AnalyticsError("Could not detect a canton column. Pass canton_column explicitly.")
        output = output_table_name or f"{normalize_identifier(table_name)}_cantons"
        value_expr = f"UPPER(TRIM(CAST({quote_identifier(column)} AS VARCHAR)))"
        code_case = canton_case_expression(value_expr, "code")
        abbr_case = canton_case_expression(value_expr, "abbr")
        name_case = canton_case_expression(value_expr, "name")
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            if column not in columns:
                raise AnalyticsError(f"Unknown canton column '{column}'.")
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE "{output}" AS
                SELECT *,
                       {code_case} AS normalized_canton_code,
                       {abbr_case} AS normalized_canton_abbreviation,
                       {name_case} AS normalized_canton_name
                FROM "{table_name}"
                """
            )
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            matched_rows = connection.execute(
                f'SELECT COUNT(*) FROM "{output}" WHERE normalized_canton_code IS NOT NULL'
            ).fetchone()[0]
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"normalize-canton:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns_out,
            )
        return {
            "source_table": table_name,
            "output_table": output,
            "canton_column": column,
            "row_count": row_count,
            "matched_rows": matched_rows,
            "match_percent": round(matched_rows * 100.0 / row_count, 2) if row_count else 0.0,
            "added_columns": [
                "normalized_canton_code",
                "normalized_canton_abbreviation",
                "normalized_canton_name",
            ],
        }

    def answer_from_table(
        self,
        table_name: str,
        question: str,
        group_by: str | None = None,
        metric_column: str | None = None,
        aggregation: str = "count",
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Answer a table question with deterministic SQL-style aggregation."""

        if aggregation not in {"count", "sum", "avg", "min", "max"}:
            raise AnalyticsError("aggregation must be one of count, sum, avg, min, max.")
        semantics = self.detect_schema_semantics(table_name)
        group = group_by or infer_group_column_from_question(question, semantics)
        metric = metric_column or semantics["primary"]["metric_column"]

        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            where_sql, params = build_where(filters or {}, columns)
            if group and group not in columns:
                raise AnalyticsError(f"Unknown group_by column '{group}'.")
            if metric and metric not in columns:
                raise AnalyticsError(f"Unknown metric column '{metric}'.")

            if not group and ("how many" in question.lower() or "count" in question.lower()):
                total = connection.execute(f'SELECT COUNT(*) FROM "{table_name}" {where_sql}', params).fetchone()[0]
                return {
                    "question": question,
                    "answer": f"{total} rows match the request.",
                    "operation": "count_rows",
                    "rows": [{"count": total}],
                    "caveat": "This is a deterministic count from the local table.",
                }

            if not group:
                group = semantics["primary"]["category_column"]
            if not group:
                raise AnalyticsError("Could not infer a group/category column. Pass group_by explicitly.")

            group_q = quote_identifier(group)
            if aggregation == "count" or not metric:
                metric_sql = "COUNT(*)"
                metric_label = "row_count"
            else:
                metric_q = quote_identifier(metric)
                metric_sql = f"{aggregation.upper()}({metric_q})"
                metric_label = f"{aggregation}_{metric}"
            df = connection.execute(
                f"""
                SELECT {group_q} AS group_value, {metric_sql} AS {quote_identifier(metric_label)}
                FROM "{table_name}"
                {where_sql}
                GROUP BY 1
                ORDER BY {quote_identifier(metric_label)} DESC NULLS LAST
                LIMIT ?
                """,
                [*params, max(1, min(limit, 1000))],
            ).fetchdf()

        rows = dataframe_records(df)
        answer = "No matching rows were found."
        if rows:
            top = rows[0]
            answer = f"Top result: {top['group_value']} with {top[metric_label]}."
        return {
            "question": question,
            "operation": f"{aggregation}_by_group",
            "table_name": table_name,
            "group_by": group,
            "metric_column": metric,
            "rows": rows,
            "answer": answer,
            "caveat": "Generated from local table rows only.",
        }

    def create_dashboard_from_question(
        self,
        table_name: str,
        question: str,
        session_id: str | None = None,
        title: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Create a starter dashboard from a loaded table and a natural-language question."""

        profile = self.profile_dataset(table_name)
        semantics = profile["semantics"]
        session = self.sessions.create_session(
            title=title or f"Dashboard for {table_name}",
            question=question,
            session_id=session_id or f"{table_name}-dashboard",
            overwrite=overwrite,
        )
        charts: list[dict[str, Any]] = []
        primary = semantics["primary"]
        if primary["longitude_column"] and primary["latitude_column"]:
            charts.append(
                self.sessions.add_chart(
                    session["id"],
                    table_name,
                    "Map of records",
                    "map",
                    primary["longitude_column"],
                    primary["latitude_column"],
                    primary["category_column"],
                    limit=1000,
                    column_labels={
                        primary["longitude_column"]: "Longitude",
                        primary["latitude_column"]: "Latitude",
                    },
                )
            )

        if primary["time_column"]:
            time_table = self.time_series_analysis(
                table_name=table_name,
                time_column=primary["time_column"],
                output_table_name=f"{session['id']}_time_series",
            )
            charts.append(
                self.sessions.add_chart(
                    session["id"],
                    time_table["output_table"],
                    "Records over time",
                    "line",
                    primary["time_column"],
                    "record_count",
                    limit=1000,
                    column_labels={primary["time_column"]: "Time", "record_count": "Records"},
                )
            )

        for category in choose_category_columns(profile, max_columns=4):
            count = self.create_category_count_table(
                table_name=table_name,
                category_column=category,
                output_table_name=f"{session['id']}_{normalize_identifier(category)}_counts",
                limit=20,
                split_commas=True,
            )
            charts.append(
                self.sessions.add_chart(
                    session["id"],
                    count["output_table"],
                    f"Top {category}",
                    "bar",
                    category,
                    "record_count",
                    limit=20,
                    column_labels={category: readable_label(category), "record_count": "Records"},
                )
            )

        self.sessions.add_analysis(
            session["id"],
            "Dataset profile",
            "profile",
            {
                "rows": [
                    {"Metric": "Rows", "Value": profile["row_count"]},
                    {"Metric": "Columns", "Value": profile["column_count"]},
                    {
                        "Metric": "Coordinate rows",
                        "Value": (profile["coordinate_summary"] or {}).get("coordinate_rows", 0),
                    },
                    {"Metric": "Detected time column", "Value": primary["time_column"] or ""},
                    {"Metric": "Detected metric column", "Value": primary["metric_column"] or ""},
                ]
            },
            narrative="Automatically generated from schema profiling and simple column semantics.",
        )
        rendered = self.sessions.render(session["id"])
        return {
            "session": self.sessions.load_session(session["id"]),
            "report": rendered,
            "charts_created": len(charts),
            "profile_summary": {
                "row_count": profile["row_count"],
                "column_count": profile["column_count"],
                "semantics": primary,
            },
        }

    def create_category_count_table(
        self,
        table_name: str,
        category_column: str,
        output_table_name: str | None = None,
        limit: int = 50,
        split_commas: bool = False,
    ) -> dict[str, Any]:
        """Create a count table for top category values."""

        output = output_table_name or f"{normalize_identifier(table_name)}_{normalize_identifier(category_column)}_counts"
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            if category_column not in columns:
                raise AnalyticsError(f"Unknown category column '{category_column}'.")
            col = quote_identifier(category_column)
            if split_commas:
                query = f"""
                CREATE OR REPLACE TABLE "{output}" AS
                SELECT TRIM(split_value) AS {col}, COUNT(*) AS record_count
                FROM "{table_name}",
                     UNNEST(STRING_SPLIT(CAST({col} AS VARCHAR), ',')) AS t(split_value)
                WHERE {col} IS NOT NULL AND TRIM(split_value) != ''
                GROUP BY 1
                ORDER BY record_count DESC, 1
                LIMIT {max(1, min(limit, 1000))}
                """
            else:
                query = f"""
                CREATE OR REPLACE TABLE "{output}" AS
                SELECT {col} AS {col}, COUNT(*) AS record_count
                FROM "{table_name}"
                WHERE {col} IS NOT NULL AND TRIM(CAST({col} AS VARCHAR)) != ''
                GROUP BY 1
                ORDER BY record_count DESC, 1
                LIMIT {max(1, min(limit, 1000))}
                """
            connection.execute(query)
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"category-count:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns_out,
            )
        return {
            "source_table": table_name,
            "category_column": category_column,
            "output_table": output,
            "row_count": row_count,
            "split_commas": split_commas,
        }

    def create_map_layer(
        self,
        table_name: str,
        latitude_column: str | None = None,
        longitude_column: str | None = None,
        color_column: str | None = None,
        output_table_name: str | None = None,
        session_id: str | None = None,
        title: str = "Map layer",
    ) -> dict[str, Any]:
        """Create a map-ready table and optionally add it to a dashboard session."""

        semantics = self.detect_schema_semantics(table_name)
        lat_col = latitude_column or semantics["primary"]["latitude_column"]
        lon_col = longitude_column or semantics["primary"]["longitude_column"]
        if not lat_col or not lon_col:
            raise AnalyticsError("Could not detect coordinates. Pass latitude_column and longitude_column.")
        output = output_table_name or f"{normalize_identifier(table_name)}_map_layer"
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            for column in [lat_col, lon_col, color_column]:
                if column and column not in columns:
                    raise AnalyticsError(f"Unknown column '{column}'.")
            keep = [lon_col, lat_col]
            if color_column:
                keep.append(color_column)
            for candidate in ("gemeinde", "municipality", "ort", "name", "id", "ark_url", "epoche"):
                if candidate in columns:
                    keep.append(candidate)
            select_sql = ", ".join(quote_identifier(column) for column in dict.fromkeys(keep))
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE "{output}" AS
                SELECT {select_sql}
                FROM "{table_name}"
                WHERE {quote_identifier(lat_col)} IS NOT NULL AND {quote_identifier(lon_col)} IS NOT NULL
                """
            )
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"map-layer:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns_out,
            )
        response: dict[str, Any] = {
            "source_table": table_name,
            "output_table": output,
            "row_count": row_count,
            "latitude_column": lat_col,
            "longitude_column": lon_col,
            "color_column": color_column,
        }
        if session_id:
            response["chart"] = self.sessions.add_chart(
                session_id,
                output,
                title,
                "map",
                lon_col,
                lat_col,
                color_column,
                limit=5000,
                column_labels={lon_col: "Longitude", lat_col: "Latitude"},
            )
            response["report"] = self.sessions.render(session_id)
        return response

    def spatial_summary(
        self,
        table_name: str,
        latitude_column: str | None = None,
        longitude_column: str | None = None,
        group_by: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Summarize coordinate coverage and optional grouped spatial bounds."""

        semantics = self.detect_schema_semantics(table_name)
        lat_col = latitude_column or semantics["primary"]["latitude_column"]
        lon_col = longitude_column or semantics["primary"]["longitude_column"]
        if not lat_col or not lon_col:
            raise AnalyticsError("Could not detect coordinates. Pass latitude_column and longitude_column.")
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            if group_by and group_by not in columns:
                raise AnalyticsError(f"Unknown group_by column '{group_by}'.")
            lat = quote_identifier(lat_col)
            lon = quote_identifier(lon_col)
            overall = connection.execute(
                f"""
                SELECT COUNT(*) AS coordinate_rows,
                       MIN({lat}) AS min_lat, MAX({lat}) AS max_lat,
                       MIN({lon}) AS min_lon, MAX({lon}) AS max_lon,
                       AVG({lat}) AS avg_lat, AVG({lon}) AS avg_lon
                FROM "{table_name}"
                WHERE {lat} IS NOT NULL AND {lon} IS NOT NULL
                """
            ).fetchone()
            grouped_rows: list[dict[str, Any]] = []
            if group_by:
                group = quote_identifier(group_by)
                df = connection.execute(
                    f"""
                    SELECT {group} AS group_value,
                           COUNT(*) AS coordinate_rows,
                           MIN({lat}) AS min_lat,
                           MAX({lat}) AS max_lat,
                           MIN({lon}) AS min_lon,
                           MAX({lon}) AS max_lon,
                           AVG({lat}) AS avg_lat,
                           AVG({lon}) AS avg_lon
                    FROM "{table_name}"
                    WHERE {lat} IS NOT NULL AND {lon} IS NOT NULL AND {group} IS NOT NULL
                    GROUP BY 1
                    ORDER BY coordinate_rows DESC
                    LIMIT ?
                    """,
                    [max(1, min(limit, 1000))],
                ).fetchdf()
                grouped_rows = dataframe_records(df)
        return {
            "table_name": table_name,
            "latitude_column": lat_col,
            "longitude_column": lon_col,
            "overall": {
                "coordinate_rows": overall[0],
                "bbox": {
                    "min_lat": overall[1],
                    "max_lat": overall[2],
                    "min_lon": overall[3],
                    "max_lon": overall[4],
                },
                "center": {"lat": overall[5], "lon": overall[6]},
            },
            "group_by": group_by,
            "groups": grouped_rows,
        }

    def correlation_analysis(
        self,
        table_name: str,
        x_column: str,
        y_column: str,
        group_column: str | None = None,
        filters: dict[str, Any] | None = None,
        output_table_name: str | None = None,
        session_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Calculate Pearson/Spearman correlation and optional scatter chart."""

        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            for column in (x_column, y_column, group_column):
                if column and column not in columns:
                    raise AnalyticsError(f"Unknown column '{column}'.")
            where_sql, params = build_where(filters or {}, columns)
            selected = [x_column, y_column] + ([group_column] if group_column else [])
            select_sql = ", ".join(quote_identifier(column) for column in selected)
            df = connection.execute(
                f"""
                SELECT {select_sql}
                FROM "{table_name}"
                {where_sql}
                WHERE_SUFFIX
                """.replace(
                    "WHERE_SUFFIX",
                    (
                        "AND" if where_sql else "WHERE"
                    )
                    + f" {quote_identifier(x_column)} IS NOT NULL AND {quote_identifier(y_column)} IS NOT NULL",
                ),
                params,
            ).fetchdf()

        if len(df) < 2:
            raise AnalyticsError("Need at least two complete rows for correlation.")
        x = pd.to_numeric(df[x_column], errors="coerce")
        y = pd.to_numeric(df[y_column], errors="coerce")
        valid = x.notna() & y.notna()
        df = df[valid].copy()
        x = x[valid]
        y = y[valid]
        pearson = float(x.corr(y, method="pearson"))
        spearman = float(x.rank().corr(y.rank(), method="pearson"))
        r_squared = pearson * pearson if not math.isnan(pearson) else math.nan
        slope = float(((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum())
        intercept = float(y.mean() - slope * x.mean())
        df["predicted_y"] = slope * x + intercept
        df["residual_y"] = y - df["predicted_y"]

        output = output_table_name or f"{normalize_identifier(table_name)}_{normalize_identifier(x_column)}_{normalize_identifier(y_column)}_correlation"
        with self.store.connect() as connection:
            connection.register("correlation_df", df)
            connection.execute(f'CREATE OR REPLACE TABLE "{output}" AS SELECT * FROM correlation_df')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"correlation:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns_out,
            )

        result: dict[str, Any] = {
            "table_name": table_name,
            "output_table": output,
            "x_column": x_column,
            "y_column": y_column,
            "group_column": group_column,
            "row_count": int(len(df)),
            "pearson_correlation": round(pearson, 6),
            "spearman_rank_correlation": round(spearman, 6),
            "r_squared": round(r_squared, 6),
            "linear_fit": {"slope": round(slope, 8), "intercept": round(intercept, 4)},
            "rows": dataframe_records(df.head(100)),
            "caveat": "Correlation is not causation; check confounders and normalize where appropriate.",
        }
        if session_id:
            chart_title = title or f"{x_column} vs {y_column}"
            result["chart"] = self.sessions.add_chart(
                session_id,
                output,
                chart_title,
                "scatter",
                x_column,
                y_column,
                group_column,
                limit=1000,
            )
            analysis_payload = json.loads(
                json.dumps(
                    {key: value for key, value in result.items() if key not in {"chart", "saved_analysis", "report"}},
                    ensure_ascii=False,
                    default=str,
                )
            )
            result["saved_analysis"] = self.sessions.add_analysis(
                session_id,
                f"Correlation: {x_column} and {y_column}",
                "correlation",
                analysis_payload,
                narrative=(
                    f"Pearson r={result['pearson_correlation']}, "
                    f"Spearman r={result['spearman_rank_correlation']}."
                ),
            )
            result["report"] = self.sessions.render(session_id)
        return result

    def correlation_matrix_analysis(
        self,
        table_name: str,
        numeric_columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        output_table_name: str | None = None,
        session_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a long-form Pearson correlation matrix table and optional heatmap."""

        profile = self.profile_dataset(table_name, max_columns=120, top_k=5)
        available_numeric = [
            column["name"]
            for column in profile["columns"]
            if "metric" in column.get("semantic_roles", []) and column["name"] not in profile["semantics"]["roles"]["time"]
        ]
        selected = numeric_columns or available_numeric[:12]
        if len(selected) < 2:
            raise AnalyticsError("Need at least two numeric columns for a correlation matrix.")
        unknown = sorted(set(selected) - set(available_numeric))
        if unknown:
            raise AnalyticsError(f"Unknown or non-numeric columns for correlation matrix: {unknown}")

        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            table_columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            where_sql, params = build_where(filters or {}, table_columns)
            select_sql = ", ".join(quote_identifier(column) for column in selected)
            df = connection.execute(
                f'SELECT {select_sql} FROM "{table_name}" {where_sql}',
                params,
            ).fetchdf()

        numeric_df = df.apply(pd.to_numeric, errors="coerce")
        usable_columns = [
            column
            for column in numeric_df.columns
            if numeric_df[column].notna().sum() >= 2 and numeric_df[column].nunique(dropna=True) > 1
        ]
        if len(usable_columns) < 2:
            raise AnalyticsError("Need at least two numeric columns with variation for a correlation matrix.")
        corr = numeric_df[usable_columns].corr(method="pearson")
        rows = [
            {
                "metric_x": left,
                "metric_y": right,
                "correlation": None if pd.isna(value) else round(float(value), 6),
            }
            for left, values in corr.iterrows()
            for right, value in values.items()
        ]
        output = output_table_name or f"{normalize_identifier(table_name)}_correlation_matrix"
        matrix_df = pd.DataFrame(rows)
        with self.store.connect() as connection:
            connection.register("matrix_df", matrix_df)
            connection.execute(f'CREATE OR REPLACE TABLE "{output}" AS SELECT * FROM matrix_df')
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"correlation-matrix:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                len(matrix_df),
                columns_out,
            )

        result: dict[str, Any] = {
            "table_name": table_name,
            "output_table": output,
            "numeric_columns": usable_columns,
            "row_count": len(matrix_df),
            "rows": dataframe_records(matrix_df),
            "caveat": "Pairwise Pearson correlations describe linear association only.",
        }
        if session_id:
            result["chart"] = self.sessions.add_chart(
                session_id,
                output,
                title or "Correlation matrix",
                "heatmap",
                "metric_x",
                "metric_y",
                "correlation",
                limit=len(matrix_df),
                column_labels={
                    "metric_x": "Indicator",
                    "metric_y": "Indicator",
                    "correlation": "Pearson r",
                },
            )
            result["report"] = self.sessions.render(session_id)
        return result

    def time_series_analysis(
        self,
        table_name: str,
        time_column: str,
        value_column: str | None = None,
        aggregation: str = "count",
        filters: dict[str, Any] | None = None,
        output_table_name: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a time-series summary table and optional chart."""

        if aggregation not in {"count", "sum", "avg", "min", "max"}:
            raise AnalyticsError("aggregation must be one of count, sum, avg, min, max.")
        output = output_table_name or f"{normalize_identifier(table_name)}_{normalize_identifier(time_column)}_series"
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            if time_column not in columns:
                raise AnalyticsError(f"Unknown time column '{time_column}'.")
            if value_column and value_column not in columns:
                raise AnalyticsError(f"Unknown value column '{value_column}'.")
            where_sql, params = build_where(filters or {}, columns)
            time_q = quote_identifier(time_column)
            if aggregation == "count" or not value_column:
                metric_expr = "COUNT(*)"
                metric_name = "record_count"
            else:
                metric_expr = f"{aggregation.upper()}({quote_identifier(value_column)})"
                metric_name = f"{aggregation}_{value_column}"
            df = connection.execute(
                f"""
                SELECT {time_q} AS {time_q}, {metric_expr} AS {quote_identifier(metric_name)}
                FROM "{table_name}"
                {where_sql}
                GROUP BY 1
                ORDER BY 1
                """,
                params,
            ).fetchdf()
        df["absolute_change"] = df[metric_name].diff()
        df["percent_change"] = df[metric_name].pct_change().replace([float("inf"), -float("inf")], pd.NA) * 100
        with self.store.connect() as connection:
            connection.register("series_df", df)
            connection.execute(f'CREATE OR REPLACE TABLE "{output}" AS SELECT * FROM series_df')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{output}"').fetchone()[0]
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"time-series:{table_name}",
                None,
                f"derived from {table_name}",
                str(self.settings.database_path),
                row_count,
                columns_out,
            )
        result: dict[str, Any] = {
            "source_table": table_name,
            "output_table": output,
            "time_column": time_column,
            "value_column": value_column,
            "metric_column": metric_name,
            "aggregation": aggregation,
            "row_count": len(df),
            "rows": dataframe_records(df.head(100)),
        }
        if session_id:
            result["chart"] = self.sessions.add_chart(
                session_id,
                output,
                f"{metric_name} over {time_column}",
                "line",
                time_column,
                metric_name,
                limit=1000,
            )
            result["report"] = self.sessions.render(session_id)
        return result

    def outlier_detection(
        self,
        table_name: str,
        value_column: str,
        group_by: str | None = None,
        aggregation: str = "avg",
        filters: dict[str, Any] | None = None,
        z_threshold: float = 2.0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Detect simple z-score outliers for a numeric metric."""

        if aggregation not in {"sum", "avg", "min", "max"}:
            raise AnalyticsError("aggregation must be one of sum, avg, min, max.")
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            for column in (value_column, group_by):
                if column and column not in columns:
                    raise AnalyticsError(f"Unknown column '{column}'.")
            where_sql, params = build_where(filters or {}, columns)
            if group_by:
                df = connection.execute(
                    f"""
                    SELECT {quote_identifier(group_by)} AS group_value,
                           {aggregation.upper()}({quote_identifier(value_column)}) AS metric_value
                    FROM "{table_name}"
                    {where_sql}
                    GROUP BY 1
                    """,
                    params,
                ).fetchdf()
            else:
                df = connection.execute(
                    f"""
                    SELECT {quote_identifier(value_column)} AS metric_value
                    FROM "{table_name}"
                    {where_sql}
                    """,
                    params,
                ).fetchdf()
        values = pd.to_numeric(df["metric_value"], errors="coerce")
        mean = float(values.mean())
        std = float(values.std(ddof=0))
        df["z_score"] = (values - mean) / std if std else 0.0
        df["abs_z_score"] = df["z_score"].abs()
        outliers = df[df["abs_z_score"] >= z_threshold].sort_values("abs_z_score", ascending=False)
        outliers = outliers.head(max(1, min(limit, 1000)))
        return {
            "table_name": table_name,
            "value_column": value_column,
            "group_by": group_by,
            "aggregation": aggregation if group_by else None,
            "mean": mean,
            "std": std,
            "z_threshold": z_threshold,
            "outlier_count": len(outliers),
            "rows": dataframe_records(outliers),
        }

    def compare_datasets(
        self,
        left_table: str,
        right_table: str,
        left_join_column: str,
        right_join_column: str,
        left_value_column: str,
        right_value_column: str,
        output_table_name: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Join two tables and calculate correlation between selected metrics."""

        output = output_table_name or (
            f"compare_{normalize_identifier(left_table)}_{normalize_identifier(right_table)}"
        )
        with self.store.connect() as connection:
            for table in (left_table, right_table):
                ensure_table_exists(connection, table)
            left_columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{left_table}")').fetchall()]
            right_columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{right_table}")').fetchall()]
            for column in (left_join_column, left_value_column):
                if column not in left_columns:
                    raise AnalyticsError(f"Unknown left table column '{column}'.")
            for column in (right_join_column, right_value_column):
                if column not in right_columns:
                    raise AnalyticsError(f"Unknown right table column '{column}'.")
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE "{output}" AS
                SELECT
                  l.{quote_identifier(left_join_column)} AS join_value,
                  l.{quote_identifier(left_value_column)} AS left_value,
                  r.{quote_identifier(right_value_column)} AS right_value
                FROM "{left_table}" l
                INNER JOIN "{right_table}" r
                  ON CAST(l.{quote_identifier(left_join_column)} AS VARCHAR)
                   = CAST(r.{quote_identifier(right_join_column)} AS VARCHAR)
                WHERE l.{quote_identifier(left_value_column)} IS NOT NULL
                  AND r.{quote_identifier(right_value_column)} IS NOT NULL
                """
            )
            df = connection.execute(f'SELECT * FROM "{output}"').fetchdf()
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"compare:{left_table}:{right_table}",
                None,
                f"derived from {left_table} and {right_table}",
                str(self.settings.database_path),
                len(df),
                columns_out,
            )
        corr = self.correlation_analysis(
            table_name=output,
            x_column="left_value",
            y_column="right_value",
            group_column="join_value",
            session_id=session_id,
            title=f"{left_value_column} vs {right_value_column}",
        )
        return {
            "output_table": output,
            "joined_rows": len(df),
            "left_table": left_table,
            "right_table": right_table,
            "correlation": corr,
        }

    def calculate_per_capita_metric(
        self,
        numerator_table: str,
        denominator_table: str,
        numerator_join_column: str,
        denominator_join_column: str,
        numerator_value_column: str,
        denominator_value_column: str,
        multiplier: float = 100000.0,
        output_table_name: str | None = None,
        session_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Join two tables and calculate a normalized rate per denominator."""

        output = output_table_name or (
            f"per_capita_{normalize_identifier(numerator_table)}_{normalize_identifier(denominator_table)}"
        )
        with self.store.connect() as connection:
            for table in (numerator_table, denominator_table):
                ensure_table_exists(connection, table)
            left_columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{numerator_table}")').fetchall()
            ]
            right_columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{denominator_table}")').fetchall()
            ]
            for column in (numerator_join_column, numerator_value_column):
                if column not in left_columns:
                    raise AnalyticsError(f"Unknown numerator table column '{column}'.")
            for column in (denominator_join_column, denominator_value_column):
                if column not in right_columns:
                    raise AnalyticsError(f"Unknown denominator table column '{column}'.")

            connection.execute(
                f"""
                CREATE OR REPLACE TABLE "{output}" AS
                WITH numerator AS (
                  SELECT CAST({quote_identifier(numerator_join_column)} AS VARCHAR) AS join_value,
                         SUM({quote_identifier(numerator_value_column)}) AS numerator_value
                  FROM "{numerator_table}"
                  WHERE {quote_identifier(numerator_value_column)} IS NOT NULL
                  GROUP BY 1
                ),
                denominator AS (
                  SELECT CAST({quote_identifier(denominator_join_column)} AS VARCHAR) AS join_value,
                         SUM({quote_identifier(denominator_value_column)}) AS denominator_value
                  FROM "{denominator_table}"
                  WHERE {quote_identifier(denominator_value_column)} IS NOT NULL
                  GROUP BY 1
                )
                SELECT n.join_value,
                       n.numerator_value,
                       d.denominator_value,
                       CASE
                         WHEN d.denominator_value IS NULL OR d.denominator_value = 0 THEN NULL
                         ELSE n.numerator_value * ? / d.denominator_value
                       END AS per_capita_value
                FROM numerator n
                INNER JOIN denominator d USING (join_value)
                ORDER BY per_capita_value DESC NULLS LAST
                """,
                [float(multiplier)],
            )
            df = connection.execute(f'SELECT * FROM "{output}"').fetchdf()
            columns_out = [row[1] for row in connection.execute(f'PRAGMA table_info("{output}")').fetchall()]
            self.store._upsert_table_metadata(
                connection,
                output,
                f"per-capita:{numerator_table}:{denominator_table}",
                None,
                f"derived from {numerator_table} and {denominator_table}",
                str(self.settings.database_path),
                len(df),
                columns_out,
            )

        result: dict[str, Any] = {
            "output_table": output,
            "numerator_table": numerator_table,
            "denominator_table": denominator_table,
            "join_columns": {
                "numerator": numerator_join_column,
                "denominator": denominator_join_column,
            },
            "value_columns": {
                "numerator": numerator_value_column,
                "denominator": denominator_value_column,
            },
            "multiplier": multiplier,
            "row_count": len(df),
            "rows": dataframe_records(df.head(100)),
            "caveat": "This is a normalized rate. Verify the denominator matches the same place and period.",
        }
        if session_id:
            result["chart"] = self.sessions.add_chart(
                session_id,
                output,
                title or f"{readable_column_label(numerator_value_column)} per {int(multiplier):,}",
                "bar",
                "join_value",
                "per_capita_value",
                limit=100,
                column_labels={
                    "join_value": "Join value",
                    "per_capita_value": f"Rate per {int(multiplier):,}",
                },
            )
            result["report"] = self.sessions.render(session_id)
        return result

    def _join_key_score(
        self,
        left_table: str,
        right_table: str,
        left_column: dict[str, Any],
        right_column: dict[str, Any],
        left_roles: dict[str, list[str]],
        right_roles: dict[str, list[str]],
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        left_name = left_column["name"]
        right_name = right_column["name"]
        left_key = normalize_label_key(left_name)
        right_key = normalize_label_key(right_name)
        if left_key == right_key:
            score += 40
            reasons.append("normalized column names match")
        if {left_key, right_key} <= {"canton", "kanton", "kt", "canton_code", "normalized_canton_code"}:
            score += 35
            reasons.append("both columns look like canton keys")
        if {left_key, right_key} <= {"year", "jahr", "annee", "date", "datum"}:
            score += 25
            reasons.append("both columns look like time keys")

        left_role_names = set(roles_for_column(left_roles, left_name))
        right_role_names = set(roles_for_column(right_roles, right_name))
        role_overlap = (left_role_names & right_role_names) - {"metric"}
        if role_overlap:
            score += 15 * len(role_overlap)
            reasons.append(f"shared semantic roles: {', '.join(sorted(role_overlap))}")
        if "metric" in left_role_names or "metric" in right_role_names:
            score -= 20
            reasons.append("numeric metric columns are usually weak join keys")

        if score > 0:
            overlap = self._value_overlap(left_table, right_table, left_name, right_name)
            if overlap["overlap_count"]:
                score += 40 * overlap["jaccard"]
                reasons.append(
                    f"{overlap['overlap_count']} sampled values overlap "
                    f"(Jaccard {overlap['jaccard']:.2f})"
                )
            elif score < 40:
                score = 0
                reasons = []
        return score, reasons

    def _value_overlap(
        self,
        left_table: str,
        right_table: str,
        left_column: str,
        right_column: str,
        limit: int = 300,
    ) -> dict[str, Any]:
        left_values = self._distinct_values(left_table, left_column, limit)
        right_values = self._distinct_values(right_table, right_column, limit)
        if not left_values or not right_values:
            return {"overlap_count": 0, "jaccard": 0.0}
        overlap = left_values & right_values
        union = left_values | right_values
        return {
            "overlap_count": len(overlap),
            "jaccard": len(overlap) / len(union) if union else 0.0,
        }

    def _distinct_values(self, table_name: str, column: str, limit: int = 300) -> set[str]:
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            rows = connection.execute(
                f"""
                SELECT DISTINCT TRIM(CAST({quote_identifier(column)} AS VARCHAR)) AS value
                FROM "{table_name}"
                WHERE {quote_identifier(column)} IS NOT NULL
                LIMIT ?
                """,
                [max(1, min(limit, 1000))],
            ).fetchall()
        return {str(row[0]).strip().upper() for row in rows if str(row[0]).strip()}

    def _granularity_summary(self, table_name: str | None, dimensions: list[str] | None) -> dict[str, Any] | None:
        if not table_name:
            return None
        profile = self.profile_dataset(table_name, max_columns=80, top_k=5)
        primary = profile["semantics"]["primary"]
        candidate_dimensions = dimensions or [
            value
            for value in (
                primary["time_column"],
                primary["canton_column"],
                primary["municipality_column"],
                primary["category_column"],
            )
            if value
        ]
        with self.store.connect() as connection:
            ensure_table_exists(connection, table_name)
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
            unknown = sorted(set(candidate_dimensions) - set(columns))
            if unknown:
                raise AnalyticsError(f"Unknown granularity dimensions for '{table_name}': {unknown}")
            dimension_stats = []
            for column in candidate_dimensions:
                distinct_count = connection.execute(
                    f'SELECT COUNT(DISTINCT {quote_identifier(column)}) FROM "{table_name}"'
                ).fetchone()[0]
                dimension_stats.append({"column": column, "distinct_count": distinct_count})
            duplicate_groups = None
            if candidate_dimensions:
                selected = ", ".join(quote_identifier(column) for column in candidate_dimensions)
                duplicate_groups = connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM (
                      SELECT {selected}, COUNT(*) AS row_count
                      FROM "{table_name}"
                      GROUP BY {selected}
                      HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
        return {
            "table_name": table_name,
            "row_count": profile["row_count"],
            "semantics": profile["semantics"],
            "dimensions_checked": candidate_dimensions,
            "dimension_stats": dimension_stats,
            "duplicate_dimension_groups": duplicate_groups,
            "dimension_key_unique": duplicate_groups == 0 if duplicate_groups is not None else None,
        }

    def generate_citation_pack(
        self,
        table_names: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a local citation JSON file from table metadata and session citations."""

        citations: list[dict[str, Any]] = []
        table_lookup = {table["table_name"]: table for table in self.store.list_tables()}
        for table_name in table_names or []:
            table = table_lookup.get(table_name)
            if table:
                citations.append(
                    {
                        "kind": "local_table",
                        "table_name": table_name,
                        "dataset_id": table.get("dataset_id"),
                        "resource_id": table.get("resource_id"),
                        "source_url": table.get("source_url"),
                        "local_path": table.get("local_path"),
                        "row_count": table.get("row_count"),
                        "accessed_at": datetime.now(UTC).date().isoformat(),
                    }
                )
        if session_id:
            manifest = self.sessions.load_session(session_id)
            for citation in manifest.get("citations", []):
                citations.append({"kind": "session_citation", **citation})
            for chart in manifest.get("charts", []):
                table = table_lookup.get(chart.get("table_name"))
                if table:
                    citations.append(
                        {
                            "kind": "chart_table",
                            "chart_id": chart.get("id"),
                            "chart_title": chart.get("title"),
                            "table_name": table["table_name"],
                            "dataset_id": table.get("dataset_id"),
                            "resource_id": table.get("resource_id"),
                            "source_url": table.get("source_url"),
                        }
                    )
        pack = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "table_names": table_names or [],
            "session_id": session_id,
            "citation_count": len(citations),
            "citations": citations,
        }
        output = self.settings.outputs_dir / f"citation-pack-{session_id or 'tables'}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
        output.write_text(json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {**pack, "path": str(output)}

    def export_session_bundle(
        self,
        session_id: str,
        include_table_csv: bool = True,
        max_rows_per_table: int = 100000,
    ) -> dict[str, Any]:
        """Export a session HTML, manifest, citations, and chart tables as a zip bundle."""

        manifest = self.sessions.load_session(session_id)
        rendered = self.sessions.render(session_id)
        bundle_dir = self.settings.outputs_dir / f"{session_id}-bundle"
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        session_dir = self.settings.sessions_dir / session_id
        shutil.copy2(session_dir / "index.html", bundle_dir / "index.html")
        shutil.copy2(session_dir / "manifest.json", bundle_dir / "manifest.json")
        citation_pack = self.generate_citation_pack(
            table_names=list({chart["table_name"] for chart in manifest.get("charts", [])}),
            session_id=session_id,
        )
        shutil.copy2(citation_pack["path"], bundle_dir / "citations.json")
        exported_tables: list[str] = []
        if include_table_csv:
            table_dir = bundle_dir / "tables"
            table_dir.mkdir()
            with self.store.connect() as connection:
                for table_name in sorted({chart["table_name"] for chart in manifest.get("charts", [])}):
                    ensure_table_exists(connection, table_name)
                    safe_name = normalize_identifier(table_name)
                    target = table_dir / f"{safe_name}.csv"
                    escaped = str(target).replace("'", "''")
                    connection.execute(
                        f'COPY (SELECT * FROM "{table_name}" LIMIT {max(1, max_rows_per_table)}) TO \'{escaped}\' (HEADER, DELIMITER \',\')'
                    )
                    exported_tables.append(str(target))
        zip_path = self.settings.outputs_dir / f"{session_id}-bundle.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in bundle_dir.rglob("*"):
                archive.write(path, path.relative_to(bundle_dir))
        return {
            "session_id": session_id,
            "bundle_dir": str(bundle_dir),
            "zip_path": str(zip_path),
            "report": rendered,
            "exported_tables": exported_tables,
        }


def normalize_name(value: str) -> str:
    """Normalize text for heuristic matching."""

    return slugify(value, separator="_").lower()


def normalize_identifier(value: str) -> str:
    """Create a safe snake_case DuckDB identifier."""

    identifier = normalize_name(value)
    identifier = re.sub(r"[^a-zA-Z0-9_]", "_", identifier)
    if not identifier:
        identifier = "column"
    if identifier[0].isdigit():
        identifier = f"c_{identifier}"
    return identifier[:80]


def unique_name(base: str, used: set[str]) -> str:
    """Return a unique identifier based on base."""

    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def quote_identifier(value: str) -> str:
    """Quote a DuckDB identifier."""

    return '"' + value.replace('"', '""') + '"'


def is_numeric_type(col_type: str) -> bool:
    """Return whether a DuckDB type is numeric."""

    return any(token in col_type.upper() for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC"))


def should_collect_categories(col_type: str, distinct_count: int, row_count: int) -> bool:
    """Return whether to collect top values for a column."""

    if is_numeric_type(col_type):
        return False
    if distinct_count == 0:
        return False
    return distinct_count <= 100 or distinct_count <= max(10, row_count * 0.5)


def first_or_none(values: list[str]) -> str | None:
    """Return the first value or None."""

    return values[0] if values else None


def roles_for_column(roles: dict[str, list[str]], column: str) -> list[str]:
    """Return semantic role labels for a column."""

    return [role for role, columns in roles.items() if column in columns]


def readable_label(column: str) -> str:
    """Convert a column name to a readable label."""

    return column.replace("_", " ").strip().title()


def choose_category_columns(profile: dict[str, Any], max_columns: int = 4) -> list[str]:
    """Pick useful categorical columns for dashboard charts."""

    preferred = [
        "gemeinde",
        "municipality",
        "canton",
        "kanton",
        "epoche",
        "category",
        "accidentseveritycategory",
        "accidentseveritycategory_en",
        "accident_severity_category",
        "accident_severity_category_en",
        "accidenttype",
        "accidenttype_en",
        "accident_type",
        "accident_type_en",
        "roadtype",
        "roadtype_en",
        "road_type",
        "road_type_en",
        "art_der_fundstelle",
        "art_der_untersuchung",
        "archaeologische_funde",
    ]
    top_categories = profile.get("top_categories", {})
    chosen: list[str] = []
    lower_lookup = {normalize_name(name): name for name in top_categories}
    for item in preferred:
        if item in lower_lookup and lower_lookup[item] not in chosen:
            chosen.append(lower_lookup[item])
    for name, values in top_categories.items():
        if name not in chosen and 1 < len(values) <= 50:
            chosen.append(name)
        if len(chosen) >= max_columns:
            break
    return chosen[:max_columns]


def infer_group_column_from_question(question: str, semantics: dict[str, Any]) -> str | None:
    """Pick a grouping column from question text and inferred semantics."""

    lower = question.lower()
    if any(token in lower for token in ("municipality", "gemeinde", "commune")):
        return semantics["primary"]["municipality_column"]
    if any(token in lower for token in ("canton", "kanton")):
        return semantics["primary"]["canton_column"]
    if any(token in lower for token in ("year", "jahr", "time", "trend")):
        return semantics["primary"]["time_column"]
    if any(token in lower for token in ("category", "type", "kind", "epoch", "epoche")):
        for column in semantics["roles"]["category"]:
            normalized = normalize_name(column)
            if "epoche" in normalized or "type" in normalized or "art" in normalized:
                return column
    return semantics["primary"]["category_column"]


def canton_case_expression(value_expr: str, target: str) -> str:
    """Build a DuckDB CASE expression for common canton code forms."""

    values: list[str] = []
    for code, (abbr, name) in CANTON_BY_CODE.items():
        if target == "code":
            output = code
        elif target == "abbr":
            output = abbr
        else:
            output = name
        matches = sorted({code, code.zfill(2), abbr, name.upper()})
        escaped_matches = ", ".join(sql_string(match) for match in matches)
        values.append(f"WHEN {value_expr} IN ({escaped_matches}) THEN {sql_string(output)}")
    values.append(
        "WHEN "
        f"{value_expr} IN ('CH', 'SWITZERLAND', 'SCHWEIZ', 'SUISSE', 'SVIZZERA') "
        f"THEN {sql_string('CH' if target != 'name' else 'Switzerland')}"
    )
    return "CASE " + " ".join(values) + " ELSE NULL END"


def sql_string(value: str) -> str:
    """Return a single-quoted SQL string literal."""

    return "'" + value.replace("'", "''") + "'"


def rank_resource(resource: Any) -> dict[str, Any]:
    """Score a dataset resource by expected analytics usefulness."""

    fmt = (resource.format or "").upper()
    url = resource.best_url or ""
    lower_url = url.lower()
    score = 0
    reasons: list[str] = []
    if fmt in {"PARQUET"} or "parquet" in lower_url:
        score += 100
        reasons.append("columnar analytics format")
    if fmt in {"CSV", "TSV"} or any(token in lower_url for token in ("csv", "tsv")):
        score += 90
        reasons.append("direct tabular format")
    if fmt in {"JSON", "JSONL", "GEOJSON", "JSON-LD"} or any(
        token in lower_url for token in ("json", "geojson", "jsonl")
    ):
        score += 75
        reasons.append("machine-readable JSON format")
    if any(token in lower_url for token in ("gpkg", "shp", "fgb", "kml", "gpx")):
        score += 65
        reasons.append("geospatial format")
    if fmt in {"XLS", "XLSX"}:
        score += 45
        reasons.append("spreadsheet format; conversion may be needed")
    if "wfs" in lower_url or "featureserver" in lower_url:
        score += 40
        reasons.append("queryable geospatial service")
    if "wms" in lower_url or "wmts" in lower_url:
        score += 15
        reasons.append("map service; less useful for raw analytics")
    if not url:
        score -= 100
        reasons.append("no usable URL")
    if "download" in lower_url or "exports" in lower_url:
        score += 10
        reasons.append("download/export endpoint")
    return {
        "resource_id": resource.id,
        "name": resource.name,
        "format": resource.format,
        "url": url,
        "score": score,
        "reasons": reasons,
    }
