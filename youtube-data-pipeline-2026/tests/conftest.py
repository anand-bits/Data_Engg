"""Shared helpers for importing AWS Lambda modules without AWS dependencies."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class FakeAwsClient:
    def __init__(self):
        self.calls = []
        self.objects = {}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        return {"ETag": "test-etag"}

    def get_object(self, **kwargs):
        self.calls.append(("get_object", kwargs))
        return {"Body": types.SimpleNamespace(read=lambda: self.objects[kwargs["Key"]])}

    def publish(self, **kwargs):
        self.calls.append(("publish", kwargs))
        return {"MessageId": "test-message"}


@pytest.fixture
def load_lambda(monkeypatch):
    """Import a Lambda file with lightweight boto3/awswrangler replacements."""
    clients = {}

    def client(service):
        clients.setdefault(service, FakeAwsClient())
        return clients[service]

    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = client
    wr_stub = types.ModuleType("awswrangler")
    wr_stub.s3 = types.SimpleNamespace(to_parquet=lambda **kwargs: {"paths": []})
    wr_stub.athena = types.SimpleNamespace(read_sql_query=lambda **kwargs: None)
    monkeypatch.setitem(sys.modules, "boto3", boto3_stub)
    monkeypatch.setitem(sys.modules, "awswrangler", wr_stub)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setenv("S3_BUCKET_BRONZE", "bronze-test")

    def load(relative_path, name):
        path = ROOT / relative_path
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return load, clients


@pytest.fixture
def pandas_module():
    import pandas as pd
    return pd

