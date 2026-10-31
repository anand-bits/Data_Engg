from datetime import datetime, timedelta, timezone


def test_row_count_boundary(load_lambda, pandas_module, monkeypatch):
    load, _ = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_row_count")
    monkeypatch.setattr(module, "MIN_ROW_COUNT", 2)
    assert module.check_row_count(pandas_module.DataFrame({"x": [1, 2]}), "table")["passed"] is True
    assert module.check_row_count(pandas_module.DataFrame({"x": [1]}), "table")["passed"] is False


def test_null_percentage_reports_missing_column_and_threshold(load_lambda, pandas_module, monkeypatch):
    load, _ = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_nulls")
    monkeypatch.setattr(module, "CRITICAL_COLUMNS", {"stats": ["required", "missing"]})
    monkeypatch.setattr(module, "MAX_NULL_PCT", 25)
    results = module.check_null_percentage(pandas_module.DataFrame({"required": [None, "ok", "ok", "ok"]}), "stats")
    assert bool(results[0]["passed"])
    assert results[0]["value"] == 25
    assert not bool(results[1]["passed"])
    assert "missing" in results[1]["message"]


def test_schema_and_value_range_checks(load_lambda, pandas_module):
    load, _ = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_schema_range")
    module.CRITICAL_COLUMNS = {"clean_statistics": ["views", "title"]}
    assert module.check_schema(pandas_module.DataFrame(columns=["views", "title"]), "clean_statistics")["passed"]
    schema = module.check_schema(pandas_module.DataFrame(columns=["views"]), "clean_statistics")
    assert schema["passed"] is False
    ranges = module.check_value_ranges(pandas_module.DataFrame({"views": [-1, 10, module.MAX_VIEWS + 1]}), "clean_statistics")
    assert ranges[0]["negative_count"] == 1
    assert ranges[0]["extreme_count"] == 1
    assert not bool(ranges[0]["passed"])
    assert module.check_value_ranges(pandas_module.DataFrame({"views": [1]}), "other") == []


def test_freshness_handles_recent_stale_missing_and_unparseable(load_lambda, pandas_module):
    load, _ = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_freshness")
    recent = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=module.FRESHNESS_HOURS + 1)).isoformat()
    assert module.check_freshness(pandas_module.DataFrame({"_processed_at": [recent]}), "stats")["passed"]
    assert not module.check_freshness(pandas_module.DataFrame({"_processed_at": [stale]}), "stats")["passed"]
    assert module.check_freshness(pandas_module.DataFrame({"value": [1]}), "stats")["passed"]
    assert module.check_freshness(pandas_module.DataFrame({"_processed_at": ["not-a-date"]}), "stats")["passed"]


def test_handler_aggregates_checks_and_sends_failure_alert(load_lambda, pandas_module, monkeypatch):
    load, clients = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_handler")
    frame = pandas_module.DataFrame({"video_id": ["v1"], "title": ["title"], "channel_title": ["channel"], "views": [-1], "region": ["us"]})
    monkeypatch.setattr(module.wr.athena, "read_sql_query", lambda **kwargs: frame)
    result = module.lambda_handler({"database": "db", "tables": ["clean_statistics"]}, None)
    assert result["quality_passed"] is False
    assert result["checks_total"] == len(result["details"])
    assert result["checks_passed"] < result["checks_total"]
    assert clients["sns"].calls[-1][0] == "publish"


def test_handler_records_read_errors_and_continues(load_lambda, monkeypatch):
    load, clients = load_lambda
    module = load("dataquality/data_quality_lambdas.py", "data_quality_read_error")
    def read(**kwargs):
        if kwargs["sql"].find("broken") >= 0:
            raise RuntimeError("table unavailable")
        return pandas_module.DataFrame()
    pandas_module = __import__("pandas")
    monkeypatch.setattr(module.wr.athena, "read_sql_query", read)
    result = module.lambda_handler({"tables": ["broken"]}, None)
    assert result["quality_passed"] is False
    assert result["details"][0]["check"] == "read_table"
    assert "table unavailable" in result["details"][0]["message"]
    assert clients["sns"].calls[-1][0] == "publish"
