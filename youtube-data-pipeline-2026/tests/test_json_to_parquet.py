import json

import pytest


def test_validate_category_data_rejects_empty_dataframe(load_lambda, pandas_module):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_empty")
    with pytest.raises(ValueError, match="Empty DataFrame"):
        module.validate_category_data(pandas_module.DataFrame())


def test_validate_category_data_keeps_last_duplicate(load_lambda, pandas_module):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_dedupe")
    frame = pandas_module.DataFrame({"id": ["1", "1", "2"], "snippet.title": ["old", "new", "Music"]})
    result = module.validate_category_data(frame)
    assert result["id"].tolist() == ["1", "2"]
    assert result["snippet.title"].tolist() == ["new", "Music"]


def test_records_for_s3_event_decodes_key(load_lambda):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_event")
    event = {"Records": [{"s3": {"bucket": {"name": "bronze"}, "object": {"key": "a%2Fb+file.json"}}}]}
    assert module._records_for_event(event) == [{"bucket": "bronze", "key": "a/b file.json"}]


def test_records_for_step_functions_builds_reference_keys(load_lambda):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_sfn")
    result = module._records_for_event({
        "triggered_by": "step_functions",
        "ingestion_id": "20261031_120000",
        "regions": ["US", " gb "],
    })
    assert result == [
        {"bucket": module.BRONZE_BUCKET, "key": "youtube/raw_statistics_reference_data/region=us/date=2026-10-31/us_category_id.json"},
        {"bucket": module.BRONZE_BUCKET, "key": "youtube/raw_statistics_reference_data/region=gb/date=2026-10-31/gb_category_id.json"},
    ]


@pytest.mark.parametrize("event, message", [
    ({}, "No reference JSON"),
    ({"triggered_by": "step_functions"}, "missing ingestion_id"),
    ({"triggered_by": "step_functions", "ingestion_id": "bad", "regions": ["us"]}, "Invalid ingestion_id"),
    ({"triggered_by": "step_functions", "ingestion_id": "20261031_120000", "regions": []}, "contains no successfully ingested regions"),
])
def test_records_for_event_rejects_incomplete_input(load_lambda, event, message):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_invalid_" + str(len(message)))
    with pytest.raises(ValueError, match=message):
        if event == {}:
            module.lambda_handler(event, None)
        else:
            module._records_for_event(event)


def test_handler_normalizes_valid_categories_and_writes_parquet(load_lambda, monkeypatch):
    load, clients = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_handler")
    key = "youtube/reference/region=US/categories.json"
    clients["s3"].objects[key] = json.dumps({"items": [
        {"id": "1", "snippet": {"title": "Music"}},
        {"id": "1", "snippet": {"title": "Music updated"}},
    ]}).encode()
    written = []
    monkeypatch.setattr(module.wr.s3, "to_parquet", lambda **kwargs: written.append(kwargs) or {})
    response = module.lambda_handler({"Records": [{"s3": {"bucket": {"name": "bronze"}, "object": {"key": key}}}]}, None)
    assert response["statusCode"] == 200
    assert response["processed"] == [{"key": key, "region": "US", "rows": 1}]
    assert written[0]["partition_cols"] == ["region"]
    assert written[0]["mode"] == "overwrite_partitions"
    assert written[0]["df"]["snippet.title"].tolist() == ["Music updated"]


def test_handler_alerts_and_raises_when_source_read_fails(load_lambda, monkeypatch):
    load, _ = load_lambda
    module = load("lambdas/json_to_parquet/lambda_function.py", "json_to_parquet_error")
    monkeypatch.setattr(module, "read_json_from_s3", lambda bucket, key: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(RuntimeError, match="1 object"):
        module.lambda_handler({"detail": {"bucket": {"name": "bronze"}, "object": {"key": "missing.json"}}}, None)
