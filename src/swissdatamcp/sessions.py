"""Interactive local analysis sessions for SwissDataMCP."""

from __future__ import annotations

import html
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from slugify import slugify

from swissdatamcp.config import Settings
from swissdatamcp.labels import automatic_column_labels, automatic_value_labels
from swissdatamcp.store import DataStore


class SessionError(RuntimeError):
    """Raised when an analysis session cannot be created or updated."""


class SessionService:
    """Manage persistent local interactive analysis sessions."""

    def __init__(self, settings: Settings, store: DataStore):
        self.settings = settings
        self.store = store

    def create_session(
        self,
        title: str,
        question: str = "",
        session_id: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a new local analysis session."""

        clean_id = self._clean_session_id(session_id or title)
        session_dir = self._session_dir(clean_id)
        manifest_path = self._manifest_path(clean_id)
        if manifest_path.exists() and not overwrite:
            return self.load_session(clean_id)

        session_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        manifest = {
            "id": clean_id,
            "title": title,
            "question": question,
            "created_at": now,
            "updated_at": now,
            "charts": [],
            "analyses": [],
            "citations": [],
            "notes": [],
        }
        self._save_manifest(manifest)
        return manifest

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return all saved sessions."""

        sessions: list[dict[str, Any]] = []
        for manifest_path in sorted(self.settings.sessions_dir.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            sessions.append(
                {
                    "id": manifest.get("id"),
                    "title": manifest.get("title"),
                    "question": manifest.get("question"),
                    "updated_at": manifest.get("updated_at"),
                    "chart_count": len(manifest.get("charts", [])),
                    "analysis_count": len(manifest.get("analyses", [])),
                    "report_path": str(manifest_path.parent / "index.html"),
                }
            )
        return sorted(sessions, key=lambda item: item.get("updated_at") or "", reverse=True)

    def load_session(self, session_id: str) -> dict[str, Any]:
        """Load a session manifest."""

        manifest_path = self._manifest_path(session_id)
        if not manifest_path.exists():
            raise SessionError(f"Unknown session '{session_id}'.")
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def add_chart(
        self,
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
    ) -> dict[str, Any]:
        """Add a chart spec to a session."""

        if chart_type not in {"line", "bar", "scatter", "map", "heatmap"}:
            raise SessionError("chart_type must be one of line, bar, scatter, map, heatmap.")
        if chart_type == "heatmap" and not series_column:
            raise SessionError("heatmap charts require series_column to contain the cell value.")

        self._validate_table_columns(table_name, [x_column, y_column, series_column])
        label_columns = [x_column, y_column, series_column]
        manifest = self.load_session(session_id)
        chart = {
            "id": f"{slugify(title)[:40] or 'chart'}-{uuid.uuid4().hex[:8]}",
            "title": title,
            "table_name": table_name,
            "chart_type": chart_type,
            "x_column": x_column,
            "y_column": y_column,
            "series_column": series_column,
            "filters": filters or {},
            "limit": max(1, min(limit, 5000)),
            "value_labels": deep_merge(automatic_value_labels(label_columns), value_labels or {}),
            "column_labels": {**automatic_column_labels(label_columns), **(column_labels or {})},
            "provenance": self._table_provenance(table_name),
            "created_at": utc_now(),
        }
        manifest["charts"].append(chart)
        self._touch_and_save(manifest)
        return chart

    def remove_chart(self, session_id: str, chart_id: str) -> dict[str, Any]:
        """Remove a chart spec from a session."""

        manifest = self.load_session(session_id)
        before = len(manifest.get("charts", []))
        manifest["charts"] = [chart for chart in manifest.get("charts", []) if chart.get("id") != chart_id]
        if len(manifest["charts"]) == before:
            raise SessionError(f"Unknown chart '{chart_id}' in session '{session_id}'.")
        self._touch_and_save(manifest)
        return {"session_id": session_id, "removed_chart_id": chart_id, "chart_count": len(manifest["charts"])}

    def update_chart(self, session_id: str, chart_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update selected chart fields."""

        allowed = {
            "title",
            "chart_type",
            "x_column",
            "y_column",
            "series_column",
            "filters",
            "limit",
            "value_labels",
            "column_labels",
        }
        unknown = sorted(set(updates) - allowed)
        if unknown:
            raise SessionError(f"Unsupported chart update fields: {unknown}")

        manifest = self.load_session(session_id)
        for chart in manifest.get("charts", []):
            if chart.get("id") == chart_id:
                chart.update({key: value for key, value in updates.items() if value is not None})
                self._validate_table_columns(
                    chart["table_name"],
                    [chart["x_column"], chart["y_column"], chart.get("series_column")],
                )
                label_columns = [chart["x_column"], chart["y_column"], chart.get("series_column")]
                chart["value_labels"] = deep_merge(
                    automatic_value_labels(label_columns),
                    chart.get("value_labels") or {},
                )
                chart["column_labels"] = {
                    **automatic_column_labels(label_columns),
                    **(chart.get("column_labels") or {}),
                }
                chart["provenance"] = self._table_provenance(chart["table_name"])
                chart["updated_at"] = utc_now()
                self._touch_and_save(manifest)
                return chart
        raise SessionError(f"Unknown chart '{chart_id}' in session '{session_id}'.")

    def add_analysis(
        self,
        session_id: str,
        title: str,
        analysis_type: str,
        result: dict[str, Any],
        narrative: str = "",
    ) -> dict[str, Any]:
        """Append an advanced analysis result to a session."""

        manifest = self.load_session(session_id)
        analysis = {
            "id": f"{slugify(title)[:40] or 'analysis'}-{uuid.uuid4().hex[:8]}",
            "title": title,
            "analysis_type": analysis_type,
            "narrative": narrative,
            "result": result,
            "created_at": utc_now(),
        }
        manifest["analyses"].append(analysis)
        self._touch_and_save(manifest)
        return analysis

    def render(self, session_id: str) -> dict[str, Any]:
        """Render the interactive HTML dashboard for a session."""

        manifest = self.load_session(session_id)
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            **manifest,
            "charts": [self._chart_payload(chart) for chart in manifest.get("charts", [])],
        }
        report_path = session_dir / "index.html"
        report_path.write_text(self._html(payload), encoding="utf-8")
        return {
            "session_id": session_id,
            "report_path": str(report_path),
            "relative_path": f"sessions/{session_id}/index.html",
            "chart_count": len(payload["charts"]),
            "analysis_count": len(payload.get("analyses", [])),
        }

    def _chart_payload(self, chart: dict[str, Any]) -> dict[str, Any]:
        columns = [chart["x_column"], chart["y_column"]]
        if chart.get("series_column"):
            columns.append(chart["series_column"])
        if chart.get("chart_type") == "map":
            schema = self.store.inspect_table(chart["table_name"], sample_rows=1)
            available = {column["name"] for column in schema["columns"]}
            for extra_column in (
                "gemeinde",
                "ort",
                "epoche",
                "art_der_fundstelle",
                "art_der_untersuchung",
                "archaeologische_funde",
                "ark_url",
                "name",
            ):
                if extra_column in available:
                    columns.append(extra_column)
        data = self.store.query_table(
            table_name=chart["table_name"],
            select_columns=list(dict.fromkeys(columns)),
            filters=chart.get("filters") or {},
            limit=int(chart.get("limit") or 1000),
        )
        return {**chart, "provenance": chart.get("provenance") or self._table_provenance(chart["table_name"]), "rows": data["rows"]}

    def _validate_table_columns(self, table_name: str, columns: list[str | None]) -> None:
        schema = self.store.inspect_table(table_name, sample_rows=1)
        available = {column["name"] for column in schema["columns"]}
        missing = [column for column in columns if column and column not in available]
        if missing:
            raise SessionError(f"Unknown columns for table '{table_name}': {missing}")

    def _table_provenance(self, table_name: str) -> dict[str, Any]:
        for table in self.store.list_tables():
            if table.get("table_name") == table_name:
                metadata = table.get("metadata") or {}
                return {
                    "table_name": table_name,
                    "dataset_id": table.get("dataset_id"),
                    "dataset_title": metadata.get("dataset_title"),
                    "publisher": metadata.get("publisher"),
                    "dataset_url": metadata.get("dataset_url"),
                    "license": metadata.get("license"),
                    "resource_id": table.get("resource_id"),
                    "resource_name": metadata.get("resource_name"),
                    "resource_format": metadata.get("resource_format"),
                    "resource_modified": metadata.get("resource_modified"),
                    "source_url": table.get("source_url"),
                    "local_path": table.get("local_path"),
                    "row_count": table.get("row_count"),
                    "loaded_at": table.get("loaded_at"),
                    "accessed_at": table.get("accessed_at"),
                }
        return {"table_name": table_name}

    def _html(self, payload: dict[str, Any]) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
        title = html.escape(payload.get("title") or "SwissDataMCP Analysis")
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      color-scheme: light;
      --ink: #142c44;
      --muted: #65758a;
      --line: #d9e2ec;
      --soft: #f6f8fb;
      --accent: #176b87;
      --danger: #a33b3b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #fff;
    }}
    .shell {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 100vh;
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--soft);
      padding: 24px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    main {{ padding: 28px; max-width: 1320px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; line-height: 1.15; }}
    h2 {{ margin: 0; font-size: 18px; }}
    h3 {{ margin: 0 0 12px; font-size: 15px; color: var(--muted); font-weight: 650; }}
    p {{ line-height: 1.55; }}
    .meta {{ color: var(--muted); font-size: 13px; margin: 0 0 18px; }}
    .section {{ margin: 0 0 28px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 18px;
    }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }}
    .panel-wide {{ grid-column: 1 / -1; }}
    .panel-header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .panel-title {{ display: grid; gap: 6px; min-width: 0; }}
    .chart-context {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
      max-width: 760px;
    }}
    .panel-actions {{ display: flex; gap: 8px; }}
    button {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      min-height: 30px;
      padding: 0 10px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }}
    button:hover {{ border-color: var(--accent); }}
    button.danger {{ color: var(--danger); }}
    .plot {{ width: 100%; min-height: 420px; }}
    .plot[data-chart-type="heatmap"], .plot[data-chart-type="map"] {{ min-height: 560px; }}
    .chart-metrics {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 16px 0;
    }}
    .metric-pill {{
      display: inline-flex;
      align-items: baseline;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      background: var(--soft);
      font-size: 12px;
      color: var(--muted);
    }}
    .metric-pill strong {{
      color: var(--ink);
      font-weight: 700;
    }}
    .filters {{
      padding: 0 16px 14px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .source-proof {{
      padding: 0 16px 14px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .source-proof a {{ color: var(--accent); }}
    .nav-list {{ display: grid; gap: 8px; margin-top: 16px; }}
    .nav-list a {{
      color: var(--ink);
      text-decoration: none;
      font-size: 14px;
      padding: 8px 10px;
      border-radius: 6px;
      background: #fff;
      border: 1px solid var(--line);
    }}
    .analysis-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .analysis-table th, .analysis-table td {{
      border-bottom: 1px solid var(--line);
      padding: 8px;
      text-align: left;
    }}
    .empty {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 24px;
      color: var(--muted);
    }}
    @media (max-width: 860px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; }}
      main {{ padding: 18px; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h1>{title}</h1>
      <p class="meta" id="session-meta"></p>
      <h3>Charts</h3>
      <nav class="nav-list" id="chart-nav"></nav>
    </aside>
    <main>
      <section class="section">
        <h3>Question</h3>
        <p id="question"></p>
      </section>
      <section class="section">
        <h3>Interactive Charts</h3>
        <div class="grid" id="charts"></div>
      </section>
      <section class="section">
        <h3>Advanced Analysis</h3>
        <div id="analyses"></div>
      </section>
    </main>
  </div>
  <script id="session-data" type="application/json">{payload_json}</script>
  <script>
    const session = JSON.parse(document.getElementById("session-data").textContent);
    document.getElementById("session-meta").textContent = `Updated ${{session.updated_at || ""}}`;
    document.getElementById("question").textContent = session.question || "No question saved.";

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, character => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[character]));
    }}

    function readableName(value) {{
      const known = {{
        lat: "Latitude",
        lon: "Longitude",
        record_count: "Records",
        accident_count: "Accidents",
        sum_accident_count: "Accidents",
        bicycle_accidents: "Bicycle accidents",
        left_value: "Left metric",
        right_value: "Right metric",
        join_value: "Join value",
        metric_x: "Indicator",
        metric_y: "Indicator",
        correlation: "Pearson r"
      }};
      const key = String(value || "");
      if (known[key]) return known[key];
      const words = key
        .replace(/_en$/i, "")
        .replace(/_/g, " ")
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .replace(/\\bUID\\b/g, "UID")
        .replace(/\\s+/g, " ")
        .trim();
      return words.replace(/\\w\\S*/g, word => {{
        if (["UID", "LV95", "CH"].includes(word.toUpperCase())) return word.toUpperCase();
        return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
      }});
    }}

    function columnLabel(chart, column) {{
      const labels = chart.column_labels || {{}};
      return labels[column] || readableName(column);
    }}

    function labelFor(chart, column, value) {{
      const maps = chart.value_labels || {{}};
      const columnMap = maps[column] || {{}};
      return columnMap[String(value)] || String(value);
    }}

    function displayValue(chart, column, value) {{
      return labelFor(chart, column, value);
    }}

    function formatNumber(value, maximumFractionDigits = 0) {{
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value ?? "");
      return new Intl.NumberFormat("en-CH", {{ maximumFractionDigits }}).format(number);
    }}

    function plotNumberFormat(rows, column) {{
      const values = rows.map(row => Number(row[column])).filter(Number.isFinite);
      if (!values.length) return "";
      return values.every(value => Number.isInteger(value)) ? ":,.0f" : ":,.2f";
    }}

    function readableFilter(chart, filterValue, column) {{
      if (filterValue == null) return "";
      if (Array.isArray(filterValue)) {{
        return filterValue.map(value => displayValue(chart, column, value)).join(", ");
      }}
      if (typeof filterValue === "object") {{
        return Object.entries(filterValue).map(([operator, value]) => {{
          const prettyOperator = {{
            in: "is one of",
            not_in: "excludes",
            eq: "equals",
            ne: "does not equal",
            gt: ">",
            gte: ">=",
            lt: "<",
            lte: "<=",
            contains: "contains"
          }}[operator] || operator;
          return `${{prettyOperator}} ${{readableFilter(chart, value, column)}}`;
        }}).join("; ");
      }}
      return displayValue(chart, column, filterValue);
    }}

    function readableFilters(chart) {{
      const filters = chart.filters || {{}};
      const parts = Object.entries(filters).map(([column, value]) => {{
        return `${{columnLabel(chart, column)}} ${{readableFilter(chart, value, column)}}`;
      }});
      return parts.length ? parts.join(" | ") : "None";
    }}

    function sourceProofHtml(chart) {{
      const source = chart.provenance || {{}};
      const parts = [];
      if (source.dataset_title) parts.push(`Dataset: ${{escapeHtml(source.dataset_title)}}`);
      else if (source.dataset_id) parts.push(`Dataset: ${{escapeHtml(source.dataset_id)}}`);
      if (source.publisher) parts.push(`Publisher: ${{escapeHtml(source.publisher)}}`);
      if (source.resource_name) parts.push(`Resource: ${{escapeHtml(source.resource_name)}}`);
      else if (source.resource_id) parts.push(`Resource: ${{escapeHtml(source.resource_id)}}`);
      if (source.accessed_at) parts.push(`Accessed: ${{escapeHtml(source.accessed_at)}}`);
      if (source.row_count != null) parts.push(`Rows: ${{formatNumber(source.row_count)}}`);
      const sourceText = parts.length ? parts.join(" | ") : `Table: ${{escapeHtml(chart.table_name)}}`;
      if (source.source_url && /^https?:\\/\\//i.test(source.source_url)) {{
        return `${{sourceText}} | <a href="${{escapeHtml(source.source_url)}}" target="_blank" rel="noreferrer">Source URL</a>`;
      }}
      return source.source_url ? `${{sourceText}} | Source: ${{escapeHtml(source.source_url)}}` : sourceText;
    }}

    function groupedRows(rows, key) {{
      const groups = new Map();
      for (const row of rows) {{
        const group = row[key] == null ? "Unknown" : String(row[key]);
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(row);
      }}
      return groups;
    }}

    function traceType(chart) {{
      if (chart.chart_type === "map") return "scattermapbox";
      if (chart.chart_type === "heatmap") return "heatmap";
      return chart.chart_type === "bar" ? "bar" : "scatter";
    }}

    function traceMode(chart) {{
      if (chart.chart_type === "line") return "lines+markers";
      if (chart.chart_type === "scatter") return "markers";
      return undefined;
    }}

    function chartSummary(chart) {{
      const x = columnLabel(chart, chart.x_column);
      const y = columnLabel(chart, chart.y_column);
      const series = chart.series_column ? columnLabel(chart, chart.series_column) : "";
      if (chart.chart_type === "heatmap") {{
        return `${{series}} compares ${{x}} with ${{y}}. Unit is Pearson r from -1 to 1. Red means positive correlation, blue means negative correlation, yellow is close to zero. Correlation is not causation.`;
      }}
      if (chart.chart_type === "map") {{
        return `Map points show ${{x}} and ${{y}} coordinates${{series ? `, grouped by ${{series}}` : ""}}. Check coordinate coverage before treating missing points as absence.`;
      }}
      if (chart.chart_type === "bar") {{
        return `Bars compare ${{y}} across ${{x}}. Unit follows the source table. Use normalized rates before comparing places with different population sizes.`;
      }}
      if (chart.chart_type === "line") {{
        return `Line chart of ${{y}} over ${{x}}. Unit follows the source table. Check date filters and breaks before reading the slope as a trend.`;
      }}
      return `Scatter plot comparing ${{x}} and ${{y}}${{series ? `, grouped by ${{series}}` : ""}}. Units follow the source table. Correlation is not causation.`;
    }}

    function chartUnit(chart) {{
      if (chart.chart_type === "heatmap") return "Pearson r";
      if (chart.chart_type === "map") return "Coordinates";
      return "Source table";
    }}

    function chartMetricPills(chart) {{
      const rows = chart.rows || [];
      const metric = chart.chart_type === "heatmap"
        ? columnLabel(chart, chart.series_column)
        : chart.chart_type === "map"
          ? "Location records"
          : columnLabel(chart, chart.y_column);
      const typeLabel = {{
        line: "Line",
        bar: "Bar",
        scatter: "Scatter",
        map: "Map",
        heatmap: "Heatmap"
      }}[chart.chart_type] || readableName(chart.chart_type);
      return [
        ["Type", typeLabel],
        ["Metric", metric],
        ["Unit", chartUnit(chart)],
        ["Rows shown", formatNumber(rows.length)],
        ["Limit", formatNumber(chart.limit || rows.length)]
      ];
    }}

    function makeTraces(chart) {{
      const rows = chart.rows || [];
      if (chart.chart_type === "heatmap") {{
        const xValues = Array.from(new Set(rows.map(row => displayValue(chart, chart.x_column, row[chart.x_column]))));
        const yValues = Array.from(new Set(rows.map(row => displayValue(chart, chart.y_column, row[chart.y_column]))));
        const valueColumn = chart.series_column;
        const lookup = new Map(rows.map(row => [
          `${{displayValue(chart, chart.x_column, row[chart.x_column])}}\\u0000${{displayValue(chart, chart.y_column, row[chart.y_column])}}`,
          row[valueColumn]
        ]));
        const z = yValues.map(y => xValues.map(x => lookup.get(`${{x}}\\u0000${{y}}`) ?? null));
        const showCellText = xValues.length * yValues.length <= 144;
        return [{{
          x: xValues,
          y: yValues,
          z,
          text: showCellText ? z.map(row => row.map(value => Number.isFinite(Number(value)) ? Number(value).toFixed(2) : "")) : undefined,
          texttemplate: showCellText ? "%{{text}}" : undefined,
          textfont: {{ size: 11, color: "#142c44" }},
          type: "heatmap",
          colorscale: [
            [0, "#2c7bb6"],
            [0.5, "#ffffbf"],
            [1, "#d7191c"]
          ],
          zmin: -1,
          zmax: 1,
          colorbar: {{ title: columnLabel(chart, valueColumn) }},
          hovertemplate: `${{columnLabel(chart, chart.x_column)}}: %{{x}}<br>${{columnLabel(chart, chart.y_column)}}: %{{y}}<br>${{columnLabel(chart, valueColumn)}}: %{{z:.3f}}<extra></extra>`
        }}];
      }}
      if (chart.chart_type === "map") {{
        const makeMapTrace = (traceRows, name) => ({{
          lon: traceRows.map(row => Number(row[chart.x_column])),
          lat: traceRows.map(row => Number(row[chart.y_column])),
          text: traceRows.map(row => {{
            const place = [row.gemeinde || row.CANTON || row.name || name, row.ort].filter(Boolean).join(" - ");
            return place || "";
          }}),
          name,
          type: "scattermapbox",
          mode: "markers",
          marker: {{ size: 9, opacity: 0.75 }},
          hovertemplate: `%{{text}}<br>${{columnLabel(chart, chart.x_column)}}: %{{lon:.5f}}<br>${{columnLabel(chart, chart.y_column)}}: %{{lat:.5f}}<extra>%{{fullData.name}}</extra>`
        }});
        if (chart.series_column) {{
          return Array.from(groupedRows(rows, chart.series_column).entries()).map(([series, seriesRows]) => (
            makeMapTrace(seriesRows, labelFor(chart, chart.series_column, series))
          ));
        }}
        return [makeMapTrace(rows, chart.title)];
      }}
      if (chart.series_column) {{
        const format = plotNumberFormat(rows, chart.y_column);
        return Array.from(groupedRows(rows, chart.series_column).entries()).map(([series, seriesRows]) => ({{
          x: seriesRows.map(row => displayValue(chart, chart.x_column, row[chart.x_column])),
          y: seriesRows.map(row => row[chart.y_column]),
          name: labelFor(chart, chart.series_column, series),
          type: traceType(chart),
          mode: traceMode(chart),
          customdata: seriesRows.map(row => row[chart.x_column]),
          hovertemplate: `${{columnLabel(chart, chart.x_column)}}: %{{x}}<br>${{columnLabel(chart, chart.y_column)}}: %{{y${{format}}}}<extra>%{{fullData.name}}</extra>`
        }}));
      }}
      const format = plotNumberFormat(rows, chart.y_column);
      return [{{
        x: rows.map(row => displayValue(chart, chart.x_column, row[chart.x_column])),
        y: rows.map(row => row[chart.y_column]),
        name: columnLabel(chart, chart.y_column),
        type: traceType(chart),
        mode: traceMode(chart),
        customdata: rows.map(row => row[chart.x_column]),
        hovertemplate: `${{columnLabel(chart, chart.x_column)}}: %{{x}}<br>${{columnLabel(chart, chart.y_column)}}: %{{y${{format}}}}<extra></extra>`
      }}];
    }}

    function chartLayout(chart) {{
      const rows = chart.rows || [];
      if (chart.chart_type === "map") {{
        const lons = rows.map(row => Number(row[chart.x_column])).filter(Number.isFinite);
        const lats = rows.map(row => Number(row[chart.y_column])).filter(Number.isFinite);
        const center = {{
          lon: lons.length ? lons.reduce((sum, value) => sum + value, 0) / lons.length : 8.9,
          lat: lats.length ? lats.reduce((sum, value) => sum + value, 0) / lats.length : 47.55
        }};
        return {{
          margin: {{ l: 0, r: 0, t: 0, b: 0 }},
          legend: {{ orientation: "h" }},
          mapbox: {{
            style: "open-street-map",
            center,
            zoom: 9
          }}
        }};
      }}
      if (chart.chart_type === "heatmap") {{
        return {{
          title: "",
          xaxis: {{
            title: columnLabel(chart, chart.x_column),
            tickangle: -35,
            automargin: true
          }},
          yaxis: {{
            title: columnLabel(chart, chart.y_column),
            automargin: true,
            autorange: "reversed"
          }},
          margin: {{ l: 150, r: 24, t: 18, b: 150 }},
          hovermode: "closest"
        }};
      }}
      return {{
        title: "",
        xaxis: {{
          title: columnLabel(chart, chart.x_column),
          tickangle: chart.chart_type === "bar" ? -30 : 0,
          automargin: true
        }},
        yaxis: {{
          title: columnLabel(chart, chart.y_column),
          tickformat: plotNumberFormat(rows, chart.y_column).replace(":", ""),
          automargin: true
        }},
        margin: {{ l: 58, r: 24, t: 18, b: 58 }},
        legend: {{ orientation: "h" }},
        hovermode: "closest"
      }};
    }}

    function drawChart(chart) {{
      const card = document.createElement("article");
      card.className = "panel";
      if (["heatmap", "map"].includes(chart.chart_type)) card.classList.add("panel-wide");
      card.id = `card-${{chart.id}}`;
      card.innerHTML = `
        <div class="panel-header">
          <div class="panel-title">
            <h2>${{escapeHtml(chart.title)}}</h2>
            <p class="chart-context">${{escapeHtml(chartSummary(chart))}}</p>
          </div>
          <div class="panel-actions">
            <button type="button" data-action="download">Download PNG</button>
            <button type="button" class="danger" data-action="remove">Delete</button>
          </div>
        </div>
        <div class="chart-metrics"></div>
        <div class="plot" data-chart-type="${{escapeHtml(chart.chart_type)}}" id="plot-${{chart.id}}"></div>
        <div class="filters"></div>
        <div class="source-proof"></div>
      `;
      document.getElementById("charts").appendChild(card);
      card.querySelector(".chart-metrics").innerHTML = chartMetricPills(chart)
        .map(([label, value]) => `<span class="metric-pill">${{escapeHtml(label)}} <strong>${{escapeHtml(value)}}</strong></span>`)
        .join("");
      const plotId = `plot-${{chart.id}}`;
      Plotly.newPlot(plotId, makeTraces(chart), chartLayout(chart), {{ responsive: true, displaylogo: false }});
      card.querySelector(".filters").textContent = `Source table: ${{chart.table_name}} | Filters: ${{readableFilters(chart)}}`;
      card.querySelector(".source-proof").innerHTML = sourceProofHtml(chart);
      card.querySelector('[data-action="remove"]').addEventListener("click", () => card.remove());
      card.querySelector('[data-action="download"]').addEventListener("click", () => {{
        Plotly.downloadImage(plotId, {{ format: "png", filename: chart.title || chart.id }});
      }});
      const nav = document.createElement("a");
      nav.href = `#card-${{chart.id}}`;
      nav.textContent = chart.title;
      document.getElementById("chart-nav").appendChild(nav);
    }}

    function drawAnalyses() {{
      const target = document.getElementById("analyses");
      const analyses = session.analyses || [];
      if (!analyses.length) {{
        target.innerHTML = '<div class="empty">No advanced analysis saved in this session yet.</div>';
        return;
      }}
      for (const analysis of analyses) {{
        const rows = (((analysis.result || {{}}).rows) || []).slice(0, 100);
        const columns = rows.length ? Object.keys(rows[0]) : [];
        const table = document.createElement("article");
        table.className = "panel section";
        table.innerHTML = `
          <div class="panel-header"><h2>${{analysis.title}}</h2></div>
          <div style="padding: 16px;">
            <p>${{analysis.narrative || ""}}</p>
            <table class="analysis-table">
              <thead><tr>${{columns.map(col => `<th>${{col}}</th>`).join("")}}</tr></thead>
              <tbody>${{rows.map(row => `<tr>${{columns.map(col => `<td>${{row[col] ?? ""}}</td>`).join("")}}</tr>`).join("")}}</tbody>
            </table>
          </div>
        `;
        target.appendChild(table);
      }}
    }}

    if ((session.charts || []).length) {{
      session.charts.forEach(drawChart);
    }} else {{
      document.getElementById("charts").innerHTML = '<div class="empty">No charts saved in this session yet.</div>';
    }}
    drawAnalyses();
  </script>
</body>
</html>
"""

    def _session_dir(self, session_id: str) -> Path:
        return self.settings.sessions_dir / self._clean_session_id(session_id)

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        self._session_dir(manifest["id"]).mkdir(parents=True, exist_ok=True)
        self._manifest_path(manifest["id"]).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _touch_and_save(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = utc_now()
        self._save_manifest(manifest)

    def _clean_session_id(self, value: str) -> str:
        clean = slugify(value, separator="-")
        if not clean:
            clean = f"session-{uuid.uuid4().hex[:8]}"
        return clean[:80]


def utc_now() -> str:
    """Return current UTC timestamp."""

    return datetime.now(UTC).isoformat(timespec="seconds")


def deep_merge(
    base: dict[str, dict[str, str]],
    override: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Merge nested label dictionaries."""

    merged = {key: dict(value) for key, value in base.items()}
    for key, value in override.items():
        merged.setdefault(key, {}).update(value)
    return merged
