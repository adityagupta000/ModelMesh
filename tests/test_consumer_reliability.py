from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from helpers import load_module_from_path


@pytest.fixture(params=["doc", "asr"])
def consumer_module(request, monkeypatch):
    if request.param == "doc":
        inference = SimpleNamespace(extract_text=MagicMock())
        stubs = {
            "inference": inference,
            "metrics": SimpleNamespace(log_metric=MagicMock()),
        }
        rel_path = "workers/doc_ocr/consumer.py"
        unique_name = "doc_consumer"
    else:
        inference = SimpleNamespace(
            transcribe=MagicMock(),
            _load_model=MagicMock(),
        )
        stubs = {
            "inference": inference,
            "metrics": SimpleNamespace(log_metric=MagicMock()),
        }
        rel_path = "workers/asr/consumer.py"
        unique_name = "asr_consumer"

    module = load_module_from_path(
        unique_name,
        rel_path,
        stubs=stubs,
        monkeypatch=monkeypatch,
    )
    return module, inference


@pytest.mark.asyncio(scope="session")
async def test_permanent_value_error_is_failed_without_sleep(consumer_module, monkeypatch):
    module, inference = consumer_module
    metric = MagicMock()
    monkeypatch.setattr(module, "log_metric", metric)
    if hasattr(inference, "extract_text"):
        inference.extract_text.side_effect = ValueError("Invalid image data")
    else:
        inference.transcribe.side_effect = ValueError("Invalid image data")

    redis = AsyncMock()
    redis.get.return_value = b"payload"
    sleeps = []

    async def no_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    fields = {
        b"job_id": b"job-1",
        b"worker_name": module.WORKER_NAME.encode(),
        b"model_id": b"model-1",
        b"model_name": _model_name(module).encode(),
        b"model_version": b"v1",
    }

    await module.process_entry(redis, "group", "1-0", fields)

    assert redis.xack.await_count == 1
    assert redis.xadd.await_count >= 1
    assert sleeps == []
    metric.assert_called_once()
    assert metric.call_args.args[5] == "error"
    assert metric.call_args.args[6] >= 0
    failed_result = redis.setex.await_args_list[-1].args[2]
    assert "Invalid image data" in failed_result


@pytest.mark.asyncio(scope="session")
async def test_transient_runtime_error_reaches_real_failed_status(consumer_module, monkeypatch):
    module, inference = consumer_module
    metric = MagicMock()
    monkeypatch.setattr(module, "log_metric", metric)

    redis = AsyncMock()
    job_data = {
        "job_id": "job-2",
        "retry_count": "3",
        "model_id": "model-1",
        "model_name": _model_name(module),
        "model_version": "v1",
    }

    await module.handle_failure(
        redis,
        job_data,
        "2-0",
        "group",
        RuntimeError("boom"),
        12,
    )

    assert redis.xack.await_count == 1
    assert redis.xadd.await_count == 1
    dlq_fields = redis.xadd.await_args.args[1]
    assert dlq_fields["reason"] == "boom"
    failed_payload = redis.setex.await_args.args[2]
    assert "boom" in failed_payload
    metric.assert_called_once_with(
        "job-2",
        "model-1",
        _model_name(module),
        "v1",
        module.WORKER_NAME,
        "error",
        12,
    )


@pytest.mark.asyncio(scope="session")
async def test_expired_payload_is_permanent(consumer_module):
    module, inference = consumer_module
    redis = AsyncMock()
    redis.get.return_value = None

    error, elapsed_ms = await module.handle_job(
        redis,
        {"job_id": "job-3", "worker_name": module.WORKER_NAME},
    )

    assert isinstance(error, module.PermanentError)
    assert str(error) == "payload expired"
    assert elapsed_ms >= 0


@pytest.mark.asyncio(scope="session")
async def test_reclaim_stale_returns_to_zero(consumer_module):
    module, inference = consumer_module
    redis = AsyncMock()
    redis.xautoclaim.return_value = ("0-0", [], [])

    await module.reclaim_stale(redis, "group")

    redis.xautoclaim.assert_awaited_once()


def _job_fields(module, job_id="job-metrics", retry_count="0"):
    return {
        b"job_id": job_id.encode(),
        b"worker_name": module.WORKER_NAME.encode(),
        b"model_id": b"model-1",
        b"model_name": _model_name(module).encode(),
        b"model_version": b"v1",
        b"retry_count": retry_count.encode(),
        b"payload": b"test-payload",
    }


def _model_name(module):
    return "doc-ocr" if module.WORKER_NAME == "doc-ocr-worker" else "asr"


def _inference_function(inference):
    if hasattr(inference, "extract_text"):
        return inference.extract_text
    return inference.transcribe


@pytest.mark.asyncio(scope="session")
async def test_success_logs_one_metric_after_job_succeeds(consumer_module, monkeypatch):
    module, inference = consumer_module
    metric = MagicMock()
    monkeypatch.setattr(module, "log_metric", metric)
    _inference_function(inference).return_value = {"text": "ok"}

    await module.process_entry(
        AsyncMock(), "group", "1-0", _job_fields(module, "job-success")
    )

    metric.assert_called_once()
    args = metric.call_args.args
    assert args[:5] == (
        "job-success",
        "model-1",
        _model_name(module),
        "v1",
        module.WORKER_NAME,
    )
    assert args[5] == "success"
    assert args[6] >= 0


@pytest.mark.asyncio(scope="session")
async def test_transient_failures_log_only_one_terminal_error(
    consumer_module, monkeypatch
):
    module, inference = consumer_module
    metric = MagicMock()
    monkeypatch.setattr(module, "log_metric", metric)
    _inference_function(inference).side_effect = RuntimeError("temporary")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    redis = AsyncMock()
    for retry_count in range(module.MAX_RETRIES + 1):
        await module.process_entry(
            redis,
            "group",
            f"{retry_count + 1}-0",
            _job_fields(module, "job-exhausted", str(retry_count)),
        )

    metric.assert_called_once()
    assert metric.call_args.args[:6] == (
        "job-exhausted",
        "model-1",
        _model_name(module),
        "v1",
        module.WORKER_NAME,
        "error",
    )
    assert metric.call_args.args[6] >= 0


@pytest.mark.asyncio(scope="session")
async def test_transient_retry_then_success_logs_only_success(
    consumer_module, monkeypatch
):
    module, inference = consumer_module
    metric = MagicMock()
    monkeypatch.setattr(module, "log_metric", metric)
    _inference_function(inference).side_effect = [
        RuntimeError("temporary"),
        {"text": "ok"},
    ]

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    redis = AsyncMock()
    await module.process_entry(
        redis, "group", "1-0", _job_fields(module, "job-recovered", "0")
    )
    await module.process_entry(
        redis, "group", "2-0", _job_fields(module, "job-recovered", "1")
    )

    metric.assert_called_once()
    assert metric.call_args.args[:6] == (
        "job-recovered",
        "model-1",
        _model_name(module),
        "v1",
        module.WORKER_NAME,
        "success",
    )
    assert metric.call_args.args[6] >= 0
