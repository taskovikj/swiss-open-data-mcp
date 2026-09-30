import hashlib
import json

import duckdb
import pandas as pd
import pytest

from swissdatamcp.store import StoreError, dataframe_records


@pytest.mark.parametrize(
    "name",
    [
        'safe"; DROP TABLE sample; --',
        "../table",
        "swissdatamcp_tables",
        "SWISSDATAMCP_TABLES",
        "123abc",
    ],
)
def test_load_rejects_unsafe_and_reserved_names(data_store, settings, name):
    path = settings.home / "data.csv"
    path.write_text("value\n1\n", encoding="utf-8")
    with pytest.raises(StoreError):
        data_store.load_file_as_table(path, table_name=name)


def test_quoted_source_columns_null_filters_and_pagination(data_store, settings):
    path = settings.home / "data.csv"
    pd.DataFrame({'metric"name': [1, None, 3], "year": [2020, 2021, 2022]}).to_csv(
        path, index=False
    )
    table = data_store.load_file_as_table(path, table_name="sample")
    assert table.metadata["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    first = data_store.query_table("sample", limit=2)
    assert first["has_more"] and first["next_offset"] == 2
    second = data_store.query_table("sample", offset=first["next_offset"], limit=2)
    assert second["rows"][0]["year"] == 2022
    assert not second["has_more"] and second["next_offset"] is None
    assert first["rows"][1]['metric"name'] is None
    assert data_store.query_table("sample", filters={'metric"name': None})["row_count"] == 1
    assert data_store.query_table("sample", filters={'metric"name': {"ne": None}})["row_count"] == 2
    assert data_store.analyze_table("sample").row_count == 3


@pytest.mark.parametrize("format", ["csv", "json", "parquet"])
def test_export_roundtrip_and_manifest(data_store, settings, format):
    path = settings.home / "data.csv"
    path.write_text("year,value\n2020,1\n2021,2\n2022,3\n", encoding="utf-8")
    data_store.load_file_as_table(
        path, table_name="sample", source_url="https://example.com/data.csv"
    )
    exported = data_store.export_table(
        "sample", format=format, filters={"value": {"gte": 2}}, max_rows=1
    )
    assert exported["truncated"] and exported["total_matching_rows"] == 2
    assert exported["source"]["source_url"] == "https://example.com/data.csv"
    from pathlib import Path

    result_path = Path(exported["path"])
    assert exported["sha256"] == hashlib.sha256(result_path.read_bytes()).hexdigest()
    assert (
        json.loads(result_path.with_suffix(".manifest.json").read_text())["export_id"]
        == exported["export_id"]
    )
    assert data_store.load_file_as_table(result_path, table_name="roundtrip").row_count == 1


def test_non_finite_values_are_valid_json():
    records = dataframe_records(pd.DataFrame({"metric": [float("nan"), float("inf"), 1.5]}))
    assert records == [{"metric": None}, {"metric": None}, {"metric": 1.5}]
    json.dumps(records, allow_nan=False)


def test_concurrent_session_updates_keep_every_analysis(settings, data_store):
    from concurrent.futures import ThreadPoolExecutor

    from swissdatamcp.sessions import SessionService

    service = SessionService(settings, data_store)
    service.create_session("Concurrent", session_id="concurrent")
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda index: service.add_analysis(
                    "concurrent", f"Analysis {index}", "test", {"index": index}
                ),
                range(20),
            )
        )
    analyses = service.load_session("concurrent")["analyses"]
    assert len(analyses) == 20
    assert {analysis["result"]["index"] for analysis in analyses} == set(range(20))
    assert not list(settings.sessions_dir.rglob("*.part"))


def test_bad_data_does_not_replace_existing_table(data_store, settings):
    path = settings.home / "data.json"
    path.write_text('[{"value": 3}]', encoding="utf-8")
    data_store.load_file_as_table(path, table_name="sample")
    path.write_text("{broken json", encoding="utf-8")
    with pytest.raises(duckdb.Error):
        data_store.load_file_as_table(path, table_name="sample")
    assert data_store.query_table("sample")["rows"] == [{"value": 3}]


def test_session_bundle_uses_canonical_id_and_unique_directory(settings, data_store):
    from pathlib import Path

    from swissdatamcp.analytics import AnalyticsService
    from swissdatamcp.sessions import SessionService

    sessions = SessionService(settings, data_store)
    sessions.create_session("Sample", session_id="sample")
    analytics = AnalyticsService(settings, data_store, sessions)
    first = analytics.export_session_bundle("../sample", include_table_csv=False)
    second = analytics.export_session_bundle("sample", include_table_csv=False)
    assert Path(first["zip_path"]).parent == settings.outputs_dir
    assert first["zip_path"] != second["zip_path"]
    assert first["session_id"] == "sample"
    assert Path(first["zip_path"]).is_file()
