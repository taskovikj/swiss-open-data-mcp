"""Shared typed models returned by SwissDataMCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResourceSummary(BaseModel):
    """A downloadable or queryable resource belonging to an opendata.swiss dataset."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    format: str | None = None
    media_type: str | None = Field(default=None, alias="media-type")
    download_url: str | None = None
    access_url: str | None = None
    url: str | None = None
    rights: str | None = None
    issued: str | None = None
    modified: str | None = None

    @property
    def best_url(self) -> str | None:
        """Return the most useful URL for data access."""

        return self.download_url or self.access_url or self.url


class DatasetSummary(BaseModel):
    """Compact dataset metadata for search results and citations."""

    id: str
    name: str
    title: str | None = None
    description: str | None = None
    organization: str | None = None
    url: str | None = None
    license: str | None = None
    language: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)
    score: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TableReference(BaseModel):
    """A local DuckDB table produced from a resource."""

    table_name: str
    dataset_id: str | None = None
    resource_id: str | None = None
    source_url: str | None = None
    local_path: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    columns: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """Summary statistics returned after analyzing a local table."""

    table_name: str
    analysis_type: str
    row_count: int
    column_count: int
    columns: list[str]
    numeric_columns: list[str]
    text_columns: list[str]
    preview: list[dict[str, Any]]
    summary: dict[str, Any]


class ChartResult(BaseModel):
    """A generated chart artifact."""

    chart_path: str
    table_name: str
    chart_type: str
    x_column: str
    y_columns: list[str]
    title: str


class ReportResult(BaseModel):
    """A generated Markdown/HTML report artifact."""

    report_path: str
    title: str
    table_name: str | None = None
    chart_path: str | None = None

