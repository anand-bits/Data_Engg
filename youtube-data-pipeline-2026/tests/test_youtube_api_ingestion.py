import json


def test_fetch_trending_videos_builds_api_request(load_lambda, monkeypatch):
    load, _ = load_lambda
    module = load("lambdas/youtube_api_ingestion/lambda_function.py", "youtube_ingestion")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"items": []}'

    def fake_urlopen(request, timeout):
        captured.update(url=request.full_url, headers=request.headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    assert module.fetch_trending_videos("us") == {"items": []}
    assert "regionCode=us" in captured["url"]
    assert "maxResults=50" in captured["url"]
    assert captured["timeout"] == 30


def test_write_to_s3_serializes_json_and_metadata(load_lambda):
    load, clients = load_lambda
    module = load("lambdas/youtube_api_ingestion/lambda_function.py", "youtube_ingestion_write")
    result = module.write_to_s3({"items": [{"title": "café"}]}, "bucket", "key.json")
    call = clients["s3"].calls[-1][1]
    assert result == {"ETag": "test-etag"}
    assert json.loads(call["Body"].decode("utf-8")) == {"items": [{"title": "café"}]}
    assert call["ContentType"] == "application/json"
    assert call["Metadata"]["source"] == "youtube_data_api_v3"


def test_handler_writes_region_data_and_returns_success(load_lambda, monkeypatch):
    load, clients = load_lambda
    module = load("lambdas/youtube_api_ingestion/lambda_function.py", "youtube_ingestion_handler")
    module.REGIONS = ["US"]
    monkeypatch.setattr(module, "fetch_trending_videos", lambda region: {"items": [{"id": "v1"}]})
    monkeypatch.setattr(module, "fetch_video_categories", lambda region: {"items": []})
    response = module.lambda_handler({}, None)
    assert response["statusCode"] == 200
    assert response["results"] == {"success": ["us"], "failed": []}
    assert len(clients["s3"].calls) == 2
    assert all("region=us" in call[1]["Key"] for call in clients["s3"].calls)


def test_handler_records_trending_failure_and_alerts(load_lambda, monkeypatch):
    load, clients = load_lambda
    module = load("lambdas/youtube_api_ingestion/lambda_function.py", "youtube_ingestion_failure")
    module.REGIONS = ["US"]
    monkeypatch.setattr(module, "fetch_trending_videos", lambda region: (_ for _ in ()).throw(RuntimeError("api down")))
    response = module.lambda_handler({}, None)
    assert response["results"]["success"] == []
    assert response["results"]["failed"] == [{"region": "us", "type": "trending", "error": "api down"}]
    assert clients["sns"].calls[-1][0] == "publish"
