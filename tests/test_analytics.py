from swissdatamcp.analytics import AnalyticsService, rank_resource
from swissdatamcp.config import Settings, ensure_directories
from swissdatamcp.models import ResourceSummary
from swissdatamcp.sessions import SessionService
from swissdatamcp.store import DataStore


def make_service(tmp_path):
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
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "\n".join(
            [
                "year,gemeinde,epoche,lon,lat,value",
                "2023,Eschenz,Roman,8.9,47.6,10",
                "2024,Eschenz,Bronze,8.91,47.61,14",
                "2024,Arbon,Roman,9.4,47.5,5",
            ]
        ),
        encoding="utf-8",
    )
    store = DataStore(settings)
    store.load_file_as_table(csv_path, table_name="sample")
    sessions = SessionService(settings, store)
    return settings, store, sessions, AnalyticsService(settings, store, sessions)


def test_profile_semantics_and_clean_table(tmp_path):
    _, store, _, analytics = make_service(tmp_path)

    semantics = analytics.detect_schema_semantics("sample")
    profile = analytics.profile_dataset("sample")
    clean = analytics.clean_table_for_analysis("sample")

    assert semantics["primary"]["time_column"] == "year"
    assert semantics["primary"]["municipality_column"] == "gemeinde"
    assert semantics["coordinate_pair_detected"] is True
    assert profile["coordinate_summary"]["coordinate_rows"] == 3
    assert clean["output_table"] == "sample_clean"
    assert "sample_clean" in [table["table_name"] for table in store.list_tables()]


def test_dashboard_map_time_correlation_and_export(tmp_path):
    settings, _, _, analytics = make_service(tmp_path)

    dashboard = analytics.create_dashboard_from_question(
        table_name="sample",
        question="Explore sample archaeology reports.",
        session_id="sample-dashboard",
    )
    correlation = analytics.correlation_analysis(
        table_name="sample",
        x_column="lon",
        y_column="value",
        group_column="gemeinde",
        session_id="sample-dashboard",
    )
    series = analytics.time_series_analysis("sample", time_column="year")
    outliers = analytics.outlier_detection("sample", value_column="value", z_threshold=1.0)
    citations = analytics.generate_citation_pack(table_names=["sample"], session_id="sample-dashboard")
    bundle = analytics.export_session_bundle("sample-dashboard")

    assert dashboard["report"]["chart_count"] >= 3
    assert correlation["row_count"] == 3
    assert series["output_table"] == "sample_year_series"
    assert outliers["outlier_count"] >= 1
    assert citations["citation_count"] >= 1
    assert (settings.sessions_dir / "sample-dashboard" / "index.html").exists()
    assert bundle["zip_path"].endswith(".zip")


def test_rank_resource_prefers_parquet():
    parquet = ResourceSummary(id="p", name="parquet", format="PARQUET", url="https://example.test/data.parquet")
    wms = ResourceSummary(id="w", name="wms", format="XML", url="https://example.test/wms?service=WMS")

    assert rank_resource(parquet)["score"] > rank_resource(wms)["score"]
