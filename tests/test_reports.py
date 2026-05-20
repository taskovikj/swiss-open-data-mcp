from pathlib import Path

from swissdatamcp.config import Settings, ensure_directories
from swissdatamcp.reports import ReportService


def test_report_copies_chart_to_relative_assets_path(tmp_path):
    settings = Settings(
        home=tmp_path,
        cache_dir=tmp_path / "cache",
        downloads_dir=tmp_path / "downloads",
        outputs_dir=tmp_path / "outputs",
        reports_dir=tmp_path / "reports",
        sessions_dir=tmp_path / "sessions",
        database_path=tmp_path / "test.duckdb",
    )
    ensure_directories(settings)
    chart = settings.outputs_dir / "chart.png"
    chart.write_bytes(b"fake-png")

    result = ReportService(settings).create_report(
        title="Chart Report",
        chart_path=str(chart),
        format="html",
    )

    assert result.chart_path == "assets/chart.png"
    assert (settings.reports_dir / "assets" / "chart.png").exists()
    assert 'src="assets/chart.png"' in Path(result.report_path).read_text(encoding="utf-8")
