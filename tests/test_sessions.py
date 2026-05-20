from swissdatamcp.config import Settings, ensure_directories
from swissdatamcp.sessions import SessionService
from swissdatamcp.store import DataStore


def make_settings(tmp_path):
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
    return settings


def test_session_add_chart_and_render(tmp_path):
    settings = make_settings(tmp_path)
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "year,canton,value\n2000,Zurich,10\n2001,Zurich,12\n",
        encoding="utf-8",
    )
    store = DataStore(settings)
    store.load_file_as_table(csv_path, table_name="sample")
    service = SessionService(settings, store)

    session = service.create_session("Births Analysis", session_id="births-analysis")
    chart = service.add_chart(
        session_id=session["id"],
        table_name="sample",
        title="Zurich births",
        chart_type="line",
        x_column="year",
        y_column="value",
        series_column="canton",
    )
    rendered = service.render(session["id"])

    assert chart["title"] == "Zurich births"
    assert chart["column_labels"]["year"] == "Year"
    assert chart["column_labels"]["value"] == "Value"
    assert chart["value_labels"]["canton"]["ZH"] == "Zurich"
    assert chart["provenance"]["table_name"] == "sample"
    assert rendered["relative_path"] == "sessions/births-analysis/index.html"
    assert (settings.sessions_dir / "births-analysis" / "index.html").exists()


def test_calculate_change(tmp_path):
    settings = make_settings(tmp_path)
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "year,canton,value\n2000,Zurich,10\n2024,Zurich,20\n2000,Bern,10\n2024,Bern,5\n",
        encoding="utf-8",
    )
    store = DataStore(settings)
    store.load_file_as_table(csv_path, table_name="sample")

    result = store.calculate_change(
        table_name="sample",
        group_column="canton",
        time_column="year",
        value_column="value",
        start_value=2000,
        end_value=2024,
    )

    assert result["rows"][0]["group_value"] == "Zurich"
    assert result["rows"][0]["percent_change"] == 100.0


def test_session_heatmap_chart(tmp_path):
    settings = make_settings(tmp_path)
    csv_path = tmp_path / "matrix.csv"
    csv_path.write_text(
        "metric_x,metric_y,correlation\nTotal,Total,1\nTotal,Bicycle,0.82\nBicycle,Total,0.82\nBicycle,Bicycle,1\n",
        encoding="utf-8",
    )
    store = DataStore(settings)
    store.load_file_as_table(csv_path, table_name="matrix")
    service = SessionService(settings, store)

    session = service.create_session("Correlation Matrix", session_id="correlation-matrix")
    chart = service.add_chart(
        session_id=session["id"],
        table_name="matrix",
        title="Useful correlation matrix",
        chart_type="heatmap",
        x_column="metric_x",
        y_column="metric_y",
        series_column="correlation",
    )
    rendered = service.render(session["id"])
    html = (settings.sessions_dir / "correlation-matrix" / "index.html").read_text(encoding="utf-8")

    assert chart["chart_type"] == "heatmap"
    assert chart["series_column"] == "correlation"
    assert rendered["chart_count"] == 1
    assert '"chart_type": "heatmap"' in html


def test_birth_chart_labels_are_readable(tmp_path):
    settings = make_settings(tmp_path)
    csv_path = tmp_path / "births.csv"
    csv_path.write_text(
        "AGE_MOTHER,OBS_VALUE\nY30T34,31000\nY35T39,22000\n",
        encoding="utf-8",
    )
    store = DataStore(settings)
    store.load_file_as_table(csv_path, table_name="births")
    service = SessionService(settings, store)

    session = service.create_session("Births", session_id="births")
    chart = service.add_chart(
        session_id=session["id"],
        table_name="births",
        title="Births by mother age",
        chart_type="bar",
        x_column="AGE_MOTHER",
        y_column="OBS_VALUE",
    )

    assert chart["column_labels"]["AGE_MOTHER"] == "Mother age group"
    assert chart["column_labels"]["OBS_VALUE"] == "Live births"
    assert chart["value_labels"]["AGE_MOTHER"]["Y30T34"] == "30-34 years"
