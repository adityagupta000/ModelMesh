import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from helpers import load_module_from_path


@pytest.fixture(params=["doc", "asr"])
def metrics_module(request, monkeypatch):
    clickhouse_connect = SimpleNamespace(get_client=MagicMock())
    paths = {
        "doc": ("workers/doc_ocr/metrics.py", "doc_metrics"),
        "asr": ("workers/asr/metrics.py", "asr_metrics"),
    }
    rel_path, unique_name = paths[request.param]
    module = load_module_from_path(
        unique_name,
        rel_path,
        stubs={"clickhouse_connect": clickhouse_connect},
        monkeypatch=monkeypatch,
    )
    return module, clickhouse_connect


def _log_one_metric(module):
    module.log_metric("request-1", "model-1", "asr", "v1", "worker-1", "success", 17)


def test_failed_client_has_one_warning_per_backoff_window(
    metrics_module, monkeypatch, caplog
):
    module, clickhouse_connect = metrics_module
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    clickhouse_connect.get_client.side_effect = OSError("ClickHouse offline")

    with caplog.at_level(logging.WARNING):
        _log_one_metric(module)
        assert module._skip_until == 160.0

        now[0] = 159.0
        _log_one_metric(module)
        assert clickhouse_connect.get_client.call_count == 1
        assert len(caplog.records) == 1

        now[0] = 160.0
        _log_one_metric(module)

    assert clickhouse_connect.get_client.call_count == 2
    assert len(caplog.records) == 2
    assert all(
        "metric insert failed (skipping for 60s)" in record.message
        for record in caplog.records
    )


def test_client_uses_configured_host_and_port(metrics_module, monkeypatch):
    module, clickhouse_connect = metrics_module
    client = MagicMock()
    clickhouse_connect.get_client.return_value = client
    monkeypatch.setenv("CLICKHOUSE_HOST", "metrics-db")
    monkeypatch.setenv("CLICKHOUSE_PORT", "9123")

    _log_one_metric(module)

    clickhouse_connect.get_client.assert_called_once_with(host="metrics-db", port=9123)
    client.insert.assert_called_once()
