"""Local Markdown/HTML report generation."""

from __future__ import annotations

import html
import shutil
from datetime import UTC, datetime
from pathlib import Path

from slugify import slugify

from swissdatamcp.config import Settings
from swissdatamcp.models import ReportResult


class ReportService:
    """Create simple local reports for saved analyses."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create_report(
        self,
        title: str,
        question: str = "",
        narrative: str = "",
        table_name: str | None = None,
        chart_path: str | None = None,
        citation: dict | None = None,
        format: str = "html",
    ) -> ReportResult:
        """Create a local HTML or Markdown report."""

        if format not in {"html", "md"}:
            raise ValueError("format must be 'html' or 'md'.")

        safe_name = slugify(title)[:90] or "swiss-data-report"
        suffix = ".html" if format == "html" else ".md"
        report_path = self.settings.reports_dir / f"{safe_name}{suffix}"
        report_chart_path = self._prepare_report_chart(chart_path)

        generated_at = datetime.now(UTC).isoformat(timespec="seconds")
        if format == "html":
            content = self._html_report(
                title=title,
                question=question,
                narrative=narrative,
                table_name=table_name,
                chart_path=report_chart_path,
                citation=citation or {},
                generated_at=generated_at,
            )
        else:
            content = self._markdown_report(
                title=title,
                question=question,
                narrative=narrative,
                table_name=table_name,
                chart_path=report_chart_path,
                citation=citation or {},
                generated_at=generated_at,
            )

        report_path.write_text(content, encoding="utf-8")
        return ReportResult(
            report_path=str(report_path),
            title=title,
            table_name=table_name,
            chart_path=report_chart_path,
        )

    def _prepare_report_chart(self, chart_path: str | None) -> str | None:
        """Copy a chart beside reports so localhost-served reports can display it."""

        if not chart_path:
            return None
        source = Path(chart_path)
        if not source.exists():
            return chart_path
        assets_dir = self.settings.reports_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        target = assets_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return f"assets/{target.name}"

    def _html_report(
        self,
        title: str,
        question: str,
        narrative: str,
        table_name: str | None,
        chart_path: str | None,
        citation: dict,
        generated_at: str,
    ) -> str:
        chart_html = ""
        if chart_path:
            chart_html = (
                f'<section><h2>Chart</h2><img src="{html.escape(chart_path)}" alt="Chart" /></section>'
            )

        citation_items = "\n".join(
            f"<li><strong>{html.escape(str(key))}:</strong> {html.escape(str(value))}</li>"
            for key, value in citation.items()
            if value not in (None, "", [], {})
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; line-height: 1.55; max-width: 1100px; }}
    h1, h2 {{ color: #17324d; }}
    img {{ max-width: 100%; border: 1px solid #ddd; }}
    code {{ background: #f4f4f4; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p><strong>Generated:</strong> {html.escape(generated_at)}</p>
  <section>
    <h2>Question</h2>
    <p>{html.escape(question or "Not provided.")}</p>
  </section>
  <section>
    <h2>Summary</h2>
    <p>{html.escape(narrative or "No narrative was provided.")}</p>
  </section>
  {chart_html}
  <section>
    <h2>Local Table</h2>
    <p>{html.escape(table_name or "No local table attached.")}</p>
  </section>
  <section>
    <h2>Citation</h2>
    <ul>{citation_items or "<li>No citation attached.</li>"}</ul>
  </section>
</body>
</html>
"""

    def _markdown_report(
        self,
        title: str,
        question: str,
        narrative: str,
        table_name: str | None,
        chart_path: str | None,
        citation: dict,
        generated_at: str,
    ) -> str:
        citation_lines = "\n".join(
            f"- **{key}:** {value}" for key, value in citation.items() if value not in (None, "", [], {})
        )
        chart_block = f"\n![Chart]({chart_path})\n" if chart_path else "\nNo chart attached.\n"
        return f"""# {title}

Generated: {generated_at}

## Question

{question or "Not provided."}

## Summary

{narrative or "No narrative was provided."}

## Chart
{chart_block}
## Local Table

{table_name or "No local table attached."}

## Citation

{citation_lines or "No citation attached."}
"""
