from unittest.mock import AsyncMock

import pytest

from job_queue import enqueue_job
from registry import ModelRecord


@pytest.mark.asyncio(scope="session")
async def test_enqueue_job_stores_payload_before_stream_entry():
    redis = AsyncMock()
    model = ModelRecord(
        id="model-id",
        name="doc-ocr",
        version="v1",
        worker_name="doc-ocr-worker",
        protocol="http",
        endpoint="http://doc-ocr-worker:8001/infer",
        status="active",
        supports_streaming=False,
        canary_percent=0,
    )

    job_id = await enqueue_job(redis, model, b"payload", "key-id")

    setex_call = redis.setex.await_args_list[0]
    payload_key = setex_call.args[0]
    assert payload_key == f"payload:{job_id}"

    xadd_call = redis.xadd.await_args_list[0]
    fields = xadd_call.args[1]
    assert "payload" not in fields
    assert fields["job_id"] == job_id
    assert setex_call.args[1] >= 1
