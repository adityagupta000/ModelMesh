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
        inference = SimpleNamespace(transcribe=MagicMock())
        stubs = {"inference": inference}
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
        b"model_name": b"doc-ocr",
        b"model_version": b"v1",
    }

    await module.process_entry(redis, "group", "1-0", fields)

    assert redis.xack.await_count == 1
    assert redis.xadd.await_count >= 1
    assert sleeps == []
    failed_result = redis.setex.await_args_list[-1].args[2]
    assert "Invalid image data" in failed_result


@pytest.mark.asyncio(scope="session")
async def test_transient_runtime_error_reaches_real_failed_status(consumer_module, monkeypatch):
    module, inference = consumer_module

    redis = AsyncMock()
    job_data = {
        "job_id": "job-2",
        "retry_count": "3",
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
