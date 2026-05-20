from swissdatamcp.config import Settings, ensure_directories
from swissdatamcp.store import DataStore


def test_load_and_analyze_csv(tmp_path):
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
    csv_path.write_text("year,canton,value\n2000,Zurich,10\n2001,Zurich,12\n", encoding="utf-8")

    store = DataStore(settings)
    table = store.load_file_as_table(
        csv_path,
        dataset_id="test",
        resource_id="sample",
        source_url="https://example.test/sample.csv",
        metadata={
            "dataset_title": "Sample dataset",
            "publisher": "Sample publisher",
            "resource_name": "Sample resource",
        },
    )
    analysis = store.analyze_table(table.table_name)
    tables = store.list_tables()

    assert table.row_count == 2
    assert table.metadata["dataset_title"] == "Sample dataset"
    assert tables[0]["metadata"]["publisher"] == "Sample publisher"
    assert tables[0]["source_url"] == "https://example.test/sample.csv"
    assert tables[0]["accessed_at"]
    assert "year" in table.columns
    assert analysis.row_count == 2
    assert "value" in analysis.numeric_columns


def test_load_csv_with_format_hint_when_url_has_no_extension(tmp_path):
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
    data_path = tmp_path / "bfs-resource.dat"
    data_path.write_text("year,canton,value\n2000,Zurich,10\n", encoding="utf-8")

    store = DataStore(settings)
    table = store.load_file_as_table(data_path, format_hint="CSV")

    assert table.row_count == 1
    assert table.columns == ["year", "canton", "value"]
