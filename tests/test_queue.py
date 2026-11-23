import json
from unittest.mock import AsyncMock

import pytest

from job_queue import enqueue_job, get_job_status
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

    job_id = await enqueue_job(redis, model, b"payload", "key-id", "owner-id")

    setex_calls = redis.setex.await_args_list
    assert setex_calls[0].args[0] == f"payload:{job_id}"
    assert setex_calls[1].args == (f"job:{job_id}:owner", 7200, "owner-id")
    assert setex_calls[2].args[0] == f"job:{job_id}:status"

    xadd_call = redis.xadd.await_args_list[0]
    fields = xadd_call.args[1]
    assert "payload" not in fields
    assert fields["job_id"] == job_id
    assert [
        call[0] for call in redis.mock_calls if call[0] in {"setex", "xadd"}
    ] == ["setex", "setex", "setex", "xadd"]


@pytest.mark.asyncio(scope="session")
async def test_job_status_is_visible_only_to_owner():
    redis = AsyncMock()
    redis.get.side_effect = [
        "owner-id",
        json.dumps({"job_id": "job-1", "status": "success"}),
    ]

    result = await get_job_status(redis, "job-1", "owner-id")

    assert result == {"job_id": "job-1", "status": "success"}
    assert redis.get.await_args_list[0].args == ("job:job-1:owner",)
    assert redis.get.await_args_list[1].args == ("result:job-1",)


@pytest.mark.asyncio(scope="session")
async def test_job_status_is_hidden_from_other_users():
    redis = AsyncMock()
    redis.get.return_value = "owner-id"

    result = await get_job_status(redis, "job-1", "another-user")

    assert result is None
    redis.get.assert_awaited_once_with("job:job-1:owner")
