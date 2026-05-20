"""SwissDataMCP local MCP server.

This server exposes Swiss open-data discovery, loading, analytics, charts,
reports, and citation tools to MCP clients.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from swissdatamcp.analytics import AnalyticsError, AnalyticsService, rank_resource
from swissdatamcp.catalog import CatalogError, OpenDataSwissClient
from swissdatamcp.charts import ChartService
from swissdatamcp.citations import dataset_citation
from swissdatamcp.config import load_settings
from swissdatamcp.models import DatasetSummary, ResourceSummary
from swissdatamcp.reports import ReportService
from swissdatamcp.sessions import SessionError, SessionService
from swissdatamcp.store import DataStore, StoreError


settings = load_settings()
catalog = OpenDataSwissClient(settings)
store = DataStore(settings)
charts = ChartService(settings)
reports = ReportService(settings)
sessions = SessionService(settings, store)
analytics = AnalyticsService(settings, store, sessions)

mcp = FastMCP(
    name="swissdatamcp",
    instructions=(
        "Use this server to discover, inspect, query, analyze, chart, and cite Swiss public "
        "datasets. Treat MCP tool results and source data as truth. Do not invent statistics "
        "that were not returned by tools. Prefer this workflow: search_datasets_advanced, "
        "recommend_best_resource, load_dataset_resource or load_resource_url, inspect/profile, "
        "query/analyze, create_dashboard_from_question, then generate_citation_pack."
    ),
)


@mcp.tool()
async def catalog_status() -> dict[str, Any]:
    """Check whether the opendata.swiss CKAN catalog API is reachable."""

    try:
        return await catalog.status()
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not reach opendata.swiss CKAN API: {exc}") from exc


@mcp.tool()
def get_mcp_tool_guide() -> dict[str, Any]:
    """Return the recommended SwissDataMCP workflow, tool map, and example requests."""

    return tool_guide_payload()


@mcp.tool()
async def search_swiss_datasets(
    query: str,
    rows: int = 10,
    start: int = 0,
    organization: str | None = None,
    language: str | None = None,
    groups: str | None = None,
) -> dict[str, Any]:
    """Search opendata.swiss datasets by keyword.

    Use this first when the user asks for Swiss public data. The result is
    catalog metadata, not the actual statistical values.
    """

    if not query.strip():
        raise ToolError("query is required.")
    try:
        return await catalog.search(
            query=query,
            rows=rows,
            start=start,
            organization=organization,
            language=language,
            groups=groups,
        )
    except (CatalogError, Exception) as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Dataset search failed: {exc}") from exc


@mcp.tool()
async def search_datasets_advanced(
    query: str,
    rows: int = 20,
    start: int = 0,
    organization: str | None = None,
    language: str | None = None,
    groups: str | None = None,
    required_format: str | None = None,
    license_keyword: str | None = None,
) -> dict[str, Any]:
    """Search opendata.swiss with post-filters for format and license.

    Use this when a normal keyword search returns too many weak candidates.
    required_format can be values like CSV, JSON, PARQUET, GEOJSON, WFS, or WMS.
    """

    if not query.strip():
        raise ToolError("query is required.")
    try:
        result = await catalog.search(
            query=query,
            rows=max(1, min(rows, 50)),
            start=start,
            organization=organization,
            language=language,
            groups=groups,
        )
        datasets = result["datasets"]
        if required_format:
            token = required_format.lower()
            datasets = [
                dataset
                for dataset in datasets
                if any(
                    token in str(resource.get("format") or "").lower()
                    or token in str(
                        resource.get("download_url")
                        or resource.get("access_url")
                        or resource.get("url")
                        or ""
                    ).lower()
                    for resource in dataset.get("resources", [])
                )
            ]
        if license_keyword:
            token = license_keyword.lower()
            datasets = [
                dataset
                for dataset in datasets
                if token in str(dataset.get("license") or "").lower()
            ]
        for dataset in datasets:
            ranked = sorted(
                [rank_resource(resource_from_dict(resource)) for resource in dataset.get("resources", [])],
                key=lambda item: item["score"],
                reverse=True,
            )
            dataset["best_resource"] = ranked[0] if ranked else None
        return {
            **result,
            "datasets": datasets,
            "advanced_filters": {
                "required_format": required_format,
                "license_keyword": license_keyword,
            },
        }
    except (CatalogError, Exception) as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Advanced dataset search failed: {exc}") from exc


@mcp.tool()
async def get_dataset_metadata(dataset_id: str, language: str = "en") -> dict[str, Any]:
    """Return full metadata for one opendata.swiss dataset."""

    try:
        dataset = await catalog.show_dataset(dataset_id, language=language)
        return dataset.model_dump(by_alias=True, exclude={"raw"})
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not fetch dataset metadata for '{dataset_id}': {exc}") from exc


@mcp.tool()
async def list_dataset_resources(dataset_id: str, language: str = "en") -> dict[str, Any]:
    """List downloadable/queryable resources for a dataset."""

    dataset = await _dataset_or_error(dataset_id, language)
    return {
        "dataset_id": dataset.id,
        "title": dataset.title,
        "resource_count": len(dataset.resources),
        "resources": [
            {
                "index": index,
                **resource.model_dump(by_alias=True),
                "best_url": resource.best_url,
                "loadable_hint": infer_loadable_hint(resource),
            }
            for index, resource in enumerate(dataset.resources)
        ],
    }


@mcp.tool()
async def recommend_best_resource(dataset_id: str, language: str = "en") -> dict[str, Any]:
    """Rank dataset resources by expected usefulness for analytics."""

    dataset = await _dataset_or_error(dataset_id, language)
    ranked = sorted(
        [rank_resource(resource) for resource in dataset.resources],
        key=lambda item: item["score"],
        reverse=True,
    )
    return {
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "title": dataset.title,
        "best_resource": ranked[0] if ranked else None,
        "ranked_resources": ranked,
        "ranking_note": (
            "Higher scores prefer direct Parquet/CSV/JSON, then geospatial downloads/services, "
            "then spreadsheets and map-only services."
        ),
    }


@mcp.tool()
async def get_dataset_citation(
    dataset_id: str,
    resource_id: str | None = None,
    resource_index: int | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Return citation/source proof for a dataset or one resource."""

    dataset = await _dataset_or_error(dataset_id, language)
    resource = _select_resource(dataset, resource_id=resource_id, resource_index=resource_index)
    return dataset_citation(dataset, resource)


@mcp.tool()
async def load_dataset_resource(
    dataset_id: str,
    resource_id: str | None = None,
    resource_index: int | None = 0,
    language: str = "en",
    table_name: str | None = None,
) -> dict[str, Any]:
    """Download a dataset resource and load it into local DuckDB.

    Supports CSV, TSV, JSON, JSONL, NDJSON, and Parquet resources. Use
    list_dataset_resources first when the best resource is unclear.
    """

    dataset = await _dataset_or_error(dataset_id, language)
    resource = _select_resource(dataset, resource_id=resource_id, resource_index=resource_index)
    if resource is None:
        raise ToolError("No matching resource found.")
    url = resource.best_url
    if not url:
        raise ToolError("The selected resource has no download/access URL.")

    try:
        local_path = await store.download_resource(
            url,
            filename_hint=f"{dataset.name}-{resource.id or resource_index or 'resource'}",
        )
        table = store.load_file_as_table(
            local_path,
            dataset_id=dataset.id,
            resource_id=resource.id,
            source_url=url,
            table_name=table_name,
            format_hint=resource.format,
            metadata={
                "dataset_title": dataset.title,
                "publisher": dataset.organization,
                "dataset_url": dataset.url,
                "license": dataset.license,
                "resource_name": resource.name,
                "resource_format": resource.format,
                "resource_modified": resource.modified,
                "source_type": "opendata.swiss",
            },
        )
        return table.model_dump()
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not load resource: {exc}") from exc


@mcp.tool()
async def load_resource_url(
    url: str,
    dataset_id: str | None = None,
    resource_id: str | None = None,
    filename_hint: str | None = None,
    table_name: str | None = None,
    format_hint: str | None = None,
) -> dict[str, Any]:
    """Download any public CSV/JSON/Parquet resource URL and load it into DuckDB."""

    try:
        local_path = await store.download_resource(url, filename_hint=filename_hint)
        table = store.load_file_as_table(
            local_path,
            dataset_id=dataset_id,
            resource_id=resource_id,
            source_url=url,
            table_name=table_name,
            format_hint=format_hint,
            metadata={"source_type": "direct_url"},
        )
        return table.model_dump()
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not load URL resource: {exc}") from exc


@mcp.tool()
def list_local_tables() -> dict[str, Any]:
    """List local DuckDB tables previously loaded by this MCP server."""

    try:
        tables = store.list_tables()
        return {"database_path": str(settings.database_path), "tables": tables}
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not list local tables: {exc}") from exc


@mcp.tool()
def inspect_local_table(table_name: str, sample_rows: int = 10) -> dict[str, Any]:
    """Inspect schema and preview rows for a local DuckDB table."""

    try:
        return store.inspect_table(table_name, sample_rows=sample_rows)
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not inspect table '{table_name}': {exc}") from exc


@mcp.tool()
def query_local_table(
    table_name: str,
    select_columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a safe SELECT over a local DuckDB table.

    Filters use this shape:
    {"column": "exact value"} or {"column": {"gte": 2000, "lte": 2024}}.
    Supported operators: eq, ne, gt, gte, lt, lte, contains.
    """

    try:
        return store.query_table(
            table_name=table_name,
            select_columns=select_columns,
            filters=filters,
            limit=limit,
        )
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not query table '{table_name}': {exc}") from exc


@mcp.tool()
def analyze_local_table(
    table_name: str,
    group_by: str | None = None,
    value_column: str | None = None,
    limit_preview: int = 20,
) -> dict[str, Any]:
    """Analyze a local table with schema, preview, numeric stats, and optional grouping."""

    try:
        return store.analyze_table(
            table_name=table_name,
            group_by=group_by,
            value_column=value_column,
            limit_preview=limit_preview,
        ).model_dump()
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not analyze table '{table_name}': {exc}") from exc


@mcp.tool()
def profile_dataset(table_name: str, max_columns: int = 80, top_k: int = 10) -> dict[str, Any]:
    """Profile a loaded table: missingness, ranges, top categories, coordinates, semantics."""

    try:
        return analytics.profile_dataset(table_name, max_columns=max_columns, top_k=top_k)
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def detect_schema_semantics(table_name: str) -> dict[str, Any]:
    """Infer semantic roles such as time, canton, municipality, lat/lon, metrics, URLs."""

    try:
        return analytics.detect_schema_semantics(table_name)
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def clean_table_for_analysis(
    table_name: str,
    output_table_name: str | None = None,
    trim_text: bool = True,
) -> dict[str, Any]:
    """Create a normalized analysis-ready copy with snake_case columns and trimmed text."""

    try:
        return analytics.clean_table_for_analysis(
            table_name=table_name,
            output_table_name=output_table_name,
            trim_text=trim_text,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def recommend_charts_for_table(
    table_name: str,
    question: str | None = None,
    max_recommendations: int = 8,
) -> dict[str, Any]:
    """Recommend useful charts and analysis tools for a loaded local table."""

    try:
        return analytics.recommend_charts_for_table(
            table_name=table_name,
            question=question,
            max_recommendations=max_recommendations,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def suggest_join_keys(left_table: str, right_table: str, max_suggestions: int = 10) -> dict[str, Any]:
    """Suggest likely join columns between two local DuckDB tables."""

    try:
        return analytics.suggest_join_keys(
            left_table=left_table,
            right_table=right_table,
            max_suggestions=max_suggestions,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def compare_table_granularity(
    left_table: str,
    right_table: str | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Describe and compare row grain, dimensions, and duplicate groups."""

    try:
        return analytics.compare_table_granularity(
            left_table=left_table,
            right_table=right_table,
            dimensions=dimensions,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def can_correlate_tables(
    left_table: str,
    right_table: str,
    left_join_column: str | None = None,
    right_join_column: str | None = None,
    left_value_column: str | None = None,
    right_value_column: str | None = None,
) -> dict[str, Any]:
    """Check whether two tables have join keys and metrics suitable for correlation."""

    try:
        return analytics.can_correlate_tables(
            left_table=left_table,
            right_table=right_table,
            left_join_column=left_join_column,
            right_join_column=right_join_column,
            left_value_column=left_value_column,
            right_value_column=right_value_column,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def normalize_canton_codes(
    table_name: str,
    canton_column: str | None = None,
    output_table_name: str | None = None,
) -> dict[str, Any]:
    """Create a derived table with normalized Swiss canton code, abbreviation, and name."""

    try:
        return analytics.normalize_canton_codes(
            table_name=table_name,
            canton_column=canton_column,
            output_table_name=output_table_name,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def create_analysis_plan(
    question: str,
    table_name: str | None = None,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic analysis plan for a Swiss public-data question."""

    steps = [
        "Search or identify the relevant official/open dataset.",
        "Rank resources and choose the best machine-readable resource.",
        "Load the resource into local DuckDB.",
        "Profile schema, missingness, dates, categories, and coordinates.",
        "Run deterministic SQL/statistical analysis.",
        "Create charts/maps and save an interactive session.",
        "Generate citations and caveats.",
    ]
    if table_name:
        try:
            semantics = analytics.detect_schema_semantics(table_name)
        except Exception as exc:  # pragma: no cover - planning fallback
            semantics = {"error": str(exc)}
    else:
        semantics = {}
    recommended_tools = [
        "search_datasets_advanced",
        "recommend_best_resource",
        "load_dataset_resource",
        "profile_dataset",
        "create_dashboard_from_question",
        "generate_citation_pack",
    ]
    if semantics and semantics.get("coordinate_pair_detected"):
        recommended_tools.extend(["create_map_layer", "spatial_summary"])
    return {
        "question": question,
        "table_name": table_name,
        "dataset_id": dataset_id,
        "steps": steps,
        "recommended_tools": recommended_tools,
        "detected_semantics": semantics,
        "caveat": "This is a plan only; use the listed tools to execute and cite results.",
    }


@mcp.tool()
def answer_from_table(
    table_name: str,
    question: str,
    group_by: str | None = None,
    metric_column: str | None = None,
    aggregation: str = "count",
    filters: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Answer a table question using deterministic aggregation only."""

    try:
        return analytics.answer_from_table(
            table_name=table_name,
            question=question,
            group_by=group_by,
            metric_column=metric_column,
            aggregation=aggregation,
            filters=filters,
            limit=limit,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def create_dashboard_from_question(
    table_name: str,
    question: str,
    session_id: str | None = None,
    title: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Create a starter interactive dashboard from a loaded table and question."""

    try:
        return analytics.create_dashboard_from_question(
            table_name=table_name,
            question=question,
            session_id=session_id,
            title=title,
            overwrite=overwrite,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def create_map_layer(
    table_name: str,
    latitude_column: str | None = None,
    longitude_column: str | None = None,
    color_column: str | None = None,
    output_table_name: str | None = None,
    session_id: str | None = None,
    title: str = "Map layer",
) -> dict[str, Any]:
    """Create a map-ready table from coordinate columns and optionally add a map chart."""

    try:
        return analytics.create_map_layer(
            table_name=table_name,
            latitude_column=latitude_column,
            longitude_column=longitude_column,
            color_column=color_column,
            output_table_name=output_table_name,
            session_id=session_id,
            title=title,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def spatial_summary(
    table_name: str,
    latitude_column: str | None = None,
    longitude_column: str | None = None,
    group_by: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Summarize coordinate coverage, bounding boxes, centers, and optional grouped counts."""

    try:
        return analytics.spatial_summary(
            table_name=table_name,
            latitude_column=latitude_column,
            longitude_column=longitude_column,
            group_by=group_by,
            limit=limit,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def compare_datasets(
    left_table: str,
    right_table: str,
    left_join_column: str,
    right_join_column: str,
    left_value_column: str,
    right_value_column: str,
    output_table_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Join two local tables and compare selected metrics with correlation."""

    try:
        return analytics.compare_datasets(
            left_table=left_table,
            right_table=right_table,
            left_join_column=left_join_column,
            right_join_column=right_join_column,
            left_value_column=left_value_column,
            right_value_column=right_value_column,
            output_table_name=output_table_name,
            session_id=session_id,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def calculate_per_capita_metric(
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
    """Calculate a normalized rate by joining numerator and denominator tables."""

    try:
        return analytics.calculate_per_capita_metric(
            numerator_table=numerator_table,
            denominator_table=denominator_table,
            numerator_join_column=numerator_join_column,
            denominator_join_column=denominator_join_column,
            numerator_value_column=numerator_value_column,
            denominator_value_column=denominator_value_column,
            multiplier=multiplier,
            output_table_name=output_table_name,
            session_id=session_id,
            title=title,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def correlation_analysis(
    table_name: str,
    x_column: str,
    y_column: str,
    group_column: str | None = None,
    filters: dict[str, Any] | None = None,
    output_table_name: str | None = None,
    session_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Calculate Pearson/Spearman correlation and optionally save a scatter chart."""

    try:
        return analytics.correlation_analysis(
            table_name=table_name,
            x_column=x_column,
            y_column=y_column,
            group_column=group_column,
            filters=filters,
            output_table_name=output_table_name,
            session_id=session_id,
            title=title,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def correlation_matrix_analysis(
    table_name: str,
    numeric_columns: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    output_table_name: str | None = None,
    session_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Create a Pearson correlation matrix table and optional heatmap chart."""

    try:
        return analytics.correlation_matrix_analysis(
            table_name=table_name,
            numeric_columns=numeric_columns,
            filters=filters,
            output_table_name=output_table_name,
            session_id=session_id,
            title=title,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def time_series_analysis(
    table_name: str,
    time_column: str,
    value_column: str | None = None,
    aggregation: str = "count",
    filters: dict[str, Any] | None = None,
    output_table_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create a time-series summary table with change columns and optional chart."""

    try:
        return analytics.time_series_analysis(
            table_name=table_name,
            time_column=time_column,
            value_column=value_column,
            aggregation=aggregation,
            filters=filters,
            output_table_name=output_table_name,
            session_id=session_id,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def outlier_detection(
    table_name: str,
    value_column: str,
    group_by: str | None = None,
    aggregation: str = "avg",
    filters: dict[str, Any] | None = None,
    z_threshold: float = 2.0,
    limit: int = 50,
) -> dict[str, Any]:
    """Find simple z-score outliers in numeric values or grouped aggregates."""

    try:
        return analytics.outlier_detection(
            table_name=table_name,
            value_column=value_column,
            group_by=group_by,
            aggregation=aggregation,
            filters=filters,
            z_threshold=z_threshold,
            limit=limit,
        )
    except (AnalyticsError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def generate_citation_pack(
    table_names: list[str] | None = None,
    session_id: str | None = None,
    dataset_ids: list[str] | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Generate a citation JSON pack for local tables, sessions, and dataset IDs."""

    try:
        pack = analytics.generate_citation_pack(table_names=table_names, session_id=session_id)
        dataset_citations = []
        for dataset_id in dataset_ids or []:
            dataset = await _dataset_or_error(dataset_id, language)
            dataset_citations.append(dataset_citation(dataset, None))
        if dataset_citations:
            pack["citations"].extend({"kind": "dataset", **citation} for citation in dataset_citations)
            pack["citation_count"] = len(pack["citations"])
            Path(pack["path"]).write_text(json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return pack
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def export_session_bundle(
    session_id: str,
    include_table_csv: bool = True,
    max_rows_per_table: int = 100000,
) -> dict[str, Any]:
    """Export an interactive session as a zip bundle with HTML, manifest, citations, and CSVs."""

    try:
        return analytics.export_session_bundle(
            session_id=session_id,
            include_table_csv=include_table_csv,
            max_rows_per_table=max_rows_per_table,
        )
    except (AnalyticsError, StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def create_chart(
    table_name: str,
    x_column: str,
    y_columns: list[str],
    chart_type: str = "line",
    title: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Create a local PNG chart from a DuckDB table.

    Supported chart types: line, bar, scatter.
    """

    try:
        result = charts.create_chart(
            table_name=table_name,
            x_column=x_column,
            y_columns=y_columns,
            chart_type=chart_type,
            title=title,
            filters=filters,
            limit=limit,
        )
        return result.model_dump()
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not create chart: {exc}") from exc


@mcp.tool()
def preview_chart_data(
    table_name: str,
    x_column: str,
    y_columns: list[str],
    filters: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return chart-ready rows without creating an image file."""

    try:
        return charts.preview_chart_data(
            table_name=table_name,
            x_column=x_column,
            y_columns=y_columns,
            filters=filters,
            limit=limit,
        )
    except StoreError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not preview chart data: {exc}") from exc


@mcp.tool()
async def create_report(
    title: str,
    question: str = "",
    narrative: str = "",
    table_name: str | None = None,
    chart_path: str | None = None,
    citation_dataset_id: str | None = None,
    citation_resource_id: str | None = None,
    format: str = "html",
) -> dict[str, Any]:
    """Create a local Markdown or HTML report for a completed analysis."""

    citation: dict[str, Any] = {}
    if citation_dataset_id:
        dataset = await _dataset_or_error(citation_dataset_id)
        resource = _select_resource(dataset, resource_id=citation_resource_id, resource_index=None)
        citation = dataset_citation(dataset, resource)
    elif table_name:
        citation = local_table_citation(table_name)

    try:
        result = reports.create_report(
            title=title,
            question=question,
            narrative=narrative,
            table_name=table_name,
            chart_path=chart_path,
            citation=citation,
            format=format,
        )
        return result.model_dump()
    except Exception as exc:  # pragma: no cover - MCP-facing error path
        raise ToolError(f"Could not create report: {exc}") from exc


@mcp.tool()
def create_analysis_session(
    title: str,
    question: str = "",
    session_id: str | None = None,
    overwrite: bool = False,
    render: bool = True,
) -> dict[str, Any]:
    """Create a persistent local interactive analysis session."""

    try:
        manifest = sessions.create_session(
            title=title,
            question=question,
            session_id=session_id,
            overwrite=overwrite,
        )
        result: dict[str, Any] = {"session": manifest}
        if render:
            result["report"] = sessions.render(manifest["id"])
        return result
    except SessionError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def list_analysis_sessions() -> dict[str, Any]:
    """List persistent local interactive analysis sessions."""

    return {"sessions_dir": str(settings.sessions_dir), "sessions": sessions.list_sessions()}


@mcp.tool()
def get_analysis_session(session_id: str) -> dict[str, Any]:
    """Return one session manifest."""

    try:
        return sessions.load_session(session_id)
    except SessionError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def add_chart_to_session(
    session_id: str,
    table_name: str,
    title: str,
    chart_type: str,
    x_column: str,
    y_column: str,
    series_column: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 1000,
    value_labels: dict[str, dict[str, str]] | None = None,
    column_labels: dict[str, str] | None = None,
    render: bool = True,
) -> dict[str, Any]:
    """Add a Plotly-backed interactive chart to a local analysis session."""

    try:
        chart = sessions.add_chart(
            session_id=session_id,
            table_name=table_name,
            title=title,
            chart_type=chart_type,
            x_column=x_column,
            y_column=y_column,
            series_column=series_column,
            filters=filters,
            limit=limit,
            value_labels=value_labels,
            column_labels=column_labels,
        )
        result: dict[str, Any] = {"chart": chart}
        if render:
            result["report"] = sessions.render(session_id)
        return result
    except (SessionError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def remove_chart_from_session(
    session_id: str,
    chart_id: str,
    render: bool = True,
) -> dict[str, Any]:
    """Remove a chart from a local analysis session."""

    try:
        result = sessions.remove_chart(session_id=session_id, chart_id=chart_id)
        if render:
            result["report"] = sessions.render(session_id)
        return result
    except SessionError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def update_chart_in_session(
    session_id: str,
    chart_id: str,
    updates: dict[str, Any],
    render: bool = True,
) -> dict[str, Any]:
    """Update chart fields such as title, filters, columns, or chart type."""

    try:
        chart = sessions.update_chart(session_id=session_id, chart_id=chart_id, updates=updates)
        result: dict[str, Any] = {"chart": chart}
        if render:
            result["report"] = sessions.render(session_id)
        return result
    except (SessionError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def render_interactive_report(session_id: str) -> dict[str, Any]:
    """Render a session as an interactive local Plotly HTML dashboard."""

    try:
        return sessions.render(session_id)
    except (SessionError, StoreError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def calculate_change_analysis(
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
    session_id: str | None = None,
    title: str | None = None,
    narrative: str = "",
    render: bool = True,
) -> dict[str, Any]:
    """Calculate ranked change between two time values, optionally saving it to a session."""

    try:
        result = store.calculate_change(
            table_name=table_name,
            group_column=group_column,
            time_column=time_column,
            value_column=value_column,
            start_value=start_value,
            end_value=end_value,
            filters=filters,
            sort_by=sort_by,
            descending=descending,
            limit=limit,
        )
        response: dict[str, Any] = {"analysis": result}
        if session_id:
            analysis_title = title or f"Change in {value_column}: {start_value} to {end_value}"
            response["saved_analysis"] = sessions.add_analysis(
                session_id=session_id,
                title=analysis_title,
                analysis_type="change",
                result=result,
                narrative=narrative,
            )
            if render:
                response["report"] = sessions.render(session_id)
        return response
    except (StoreError, SessionError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
def get_workspace_info() -> dict[str, Any]:
    """Return local SwissDataMCP paths and configuration."""

    return {
        "home": str(settings.home),
        "cache_dir": str(settings.cache_dir),
        "downloads_dir": str(settings.downloads_dir),
        "outputs_dir": str(settings.outputs_dir),
        "reports_dir": str(settings.reports_dir),
        "sessions_dir": str(settings.sessions_dir),
        "database_path": str(settings.database_path),
        "ckan_base_url": settings.ckan_base_url,
        "max_download_mb": settings.max_download_mb,
    }


@mcp.resource("swissdatamcp://workspace")
def workspace_resource() -> str:
    """Read the SwissDataMCP local workspace configuration."""

    return json.dumps(get_workspace_info(), indent=2)


@mcp.resource("swissdatamcp://guide")
def guide_resource() -> str:
    """Read the SwissDataMCP tool workflow guide."""

    return json.dumps(tool_guide_payload(), indent=2)


@mcp.resource("swissdatamcp://tables")
def local_tables_resource() -> str:
    """Read the list of local DuckDB tables loaded by SwissDataMCP."""

    return json.dumps(list_local_tables(), indent=2)


@mcp.resource("swissdatamcp://table/{table_name}")
def local_table_resource(table_name: str) -> str:
    """Read schema and sample data for a local table."""

    return json.dumps(inspect_local_table(table_name, sample_rows=20), indent=2, default=str)


@mcp.prompt()
def analyze_swiss_statistical_question(question: str) -> str:
    """Workflow prompt for source-grounded Swiss data analysis."""

    return f"""Analyze this Swiss public-data question with SwissDataMCP:

Question: {question}

Use this workflow:
1. Call search_swiss_datasets for candidate datasets.
2. Inspect metadata and resources before choosing a dataset.
3. Load only a relevant machine-readable resource.
4. Inspect the local table schema before querying.
5. Query/analyze the table with deterministic tools.
6. Create a chart or report if useful.
7. Cite the dataset and clearly state caveats.

Do not invent numbers. If the dataset cannot answer the question, say what is missing.
"""


@mcp.prompt()
def compare_swiss_geographies(topic: str, geographies: str, time_range: str = "") -> str:
    """Workflow prompt for comparing cantons, cities, or municipalities."""

    return f"""Compare Swiss geographies using official/open datasets.

Topic: {topic}
Geographies: {geographies}
Time range: {time_range or "not specified"}

Use SwissDataMCP tools to search datasets, inspect schema, query data, create a comparison chart,
and cite sources. Ask for clarification if a geography is ambiguous, such as Zurich city vs canton.
"""


async def _dataset_or_error(dataset_id: str, language: str = "en") -> DatasetSummary:
    try:
        return await catalog.show_dataset(dataset_id, language=language)
    except Exception as exc:
        raise ToolError(f"Could not fetch dataset '{dataset_id}': {exc}") from exc


def _select_resource(
    dataset: DatasetSummary,
    resource_id: str | None = None,
    resource_index: int | None = 0,
) -> ResourceSummary | None:
    if resource_id:
        for resource in dataset.resources:
            if resource.id == resource_id:
                return resource
        return None
    if resource_index is not None:
        if resource_index < 0 or resource_index >= len(dataset.resources):
            raise ToolError(f"resource_index {resource_index} is out of range.")
        return dataset.resources[resource_index]
    return None


def infer_loadable_hint(resource: ResourceSummary) -> str:
    """Explain whether the resource is likely loadable by the MVP loader."""

    url = resource.best_url or ""
    extension = Path(url.split("?", 1)[0]).suffix.lower()
    format_label = (resource.format or "").lower()
    media_type = (resource.media_type or "").lower()
    combined = " ".join([extension, format_label, media_type])
    if any(token in combined for token in ["csv", "tsv", "json", "ndjson", "jsonl", "parquet"]):
        return "likely_loadable"
    if any(token in combined for token in ["xls", "xlsx"]):
        return "not_yet_loadable_excel"
    if any(token in combined for token in ["zip"]):
        return "not_yet_loadable_zip"
    if url:
        return "unknown_format_try_load_resource_url"
    return "no_url"


def resource_from_dict(resource: dict[str, Any]) -> ResourceSummary:
    """Rehydrate a resource dict returned by search into the typed resource model."""

    return ResourceSummary.model_validate(resource)


def local_table_citation(table_name: str) -> dict[str, Any]:
    """Build citation metadata from the local table registry."""

    for table in store.list_tables():
        if table.get("table_name") == table_name:
            metadata = table.get("metadata") or {}
            return {
                "title": metadata.get("dataset_title"),
                "publisher": metadata.get("publisher"),
                "dataset_id": table.get("dataset_id"),
                "dataset_url": metadata.get("dataset_url"),
                "resource_id": table.get("resource_id"),
                "resource_name": metadata.get("resource_name"),
                "resource_url": table.get("source_url"),
                "local_table": table_name,
                "local_path": table.get("local_path"),
                "accessed_at": table.get("accessed_at"),
            }
    return {"local_table": table_name}


def tool_guide_payload() -> dict[str, Any]:
    """Return a compact guide MCP clients can consult before using the server."""

    return {
        "purpose": (
            "Source-grounded Swiss public-data exploration. SwissDataMCP discovers datasets, "
            "loads rows, computes results, renders charts, and stores citations."
        ),
        "golden_rule": "Never invent numbers. Use local table rows and cited source metadata.",
        "recommended_workflow": [
            {
                "step": "Discover",
                "tools": ["search_datasets_advanced", "get_dataset_metadata", "list_dataset_resources"],
                "success_check": "A relevant dataset and at least one machine-readable resource were found.",
            },
            {
                "step": "Choose source",
                "tools": ["recommend_best_resource", "get_dataset_citation"],
                "success_check": "A loadable CSV/TSV/JSON/NDJSON/Parquet resource and source proof are selected.",
            },
            {
                "step": "Load and inspect",
                "tools": [
                    "load_dataset_resource",
                    "inspect_local_table",
                    "profile_dataset",
                    "detect_schema_semantics",
                    "normalize_canton_codes",
                ],
                "success_check": "The local DuckDB table has expected rows, columns, dates, categories, and metrics.",
            },
            {
                "step": "Plan compatibility",
                "tools": [
                    "recommend_charts_for_table",
                    "suggest_join_keys",
                    "compare_table_granularity",
                    "can_correlate_tables",
                ],
                "success_check": "Useful charts, join keys, table grain, and correlation readiness are known.",
            },
            {
                "step": "Analyze",
                "tools": [
                    "query_local_table",
                    "answer_from_table",
                    "time_series_analysis",
                    "correlation_analysis",
                    "correlation_matrix_analysis",
                    "calculate_per_capita_metric",
                    "outlier_detection",
                    "compare_datasets",
                ],
                "success_check": "Results are deterministic and come from SQL/table calculations.",
            },
            {
                "step": "Present",
                "tools": [
                    "create_dashboard_from_question",
                    "create_map_layer",
                    "add_chart_to_session",
                    "render_interactive_report",
                    "generate_citation_pack",
                    "export_session_bundle",
                ],
                "success_check": "The dashboard has readable chart labels, caveats, and citation files.",
            },
        ],
        "example_requests": [
            "Use swissdatamcp to find Swiss datasets about rent prices by canton and show what data is available.",
            "Load the best machine-readable dataset about Zurich traffic accidents and create an interactive dashboard.",
            "Compare yearly trends and create a correlation matrix, with citations.",
            "Find a dataset with coordinates, create a map layer, and explain the most important categories.",
        ],
        "safe_behavior": [
            "Respect public dataset terms and source URLs.",
            "Do not scrape private, login, checkout, account, or protected endpoints.",
            "Say when the data cannot answer the user's question.",
            "Do not call counts a rate unless a denominator was loaded and a rate was computed.",
            "Label correlations and predictions as analytical estimates, not confirmed facts.",
        ],
        "local_setup": {
            "install": [
                "python -m venv .venv",
                ".\\.venv\\Scripts\\python -m pip install --upgrade pip",
                ".\\.venv\\Scripts\\python -m pip install -e .",
            ],
            "doctor": "swissdatamcp doctor",
            "config": "swissdatamcp config",
            "serve_dashboards": "swissdatamcp serve --port 8787",
        },
    }


def main() -> None:
    """Run the local stdio MCP server."""

    mcp.run()


if __name__ == "__main__":
    main()
