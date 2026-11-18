import pytest

import main
import registry
from conftest import auth_header
from registry import ModelRecord
from rate_limit import check_rate_limit


pytestmark = pytest.mark.asyncio(scope="session")


class _StubResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class _StubAsyncClient:
    def __init__(self, timeout):
        self.files = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, endpoint, files):
        self.files = files
        return _StubResponse()


@pytest.mark.parametrize("model_name", ["doc-ocr", "asr", "custom-model"])
async def test_route_request_uses_octet_stream(monkeypatch, model_name):
    clients = []

    class RecordingAsyncClient(_StubAsyncClient):
        def __init__(self, timeout):
            super().__init__(timeout)
            clients.append(self)

    monkeypatch.setattr(main.httpx, "AsyncClient", RecordingAsyncClient)

    result = await main.route_request(
        "http://worker/infer",
        "http",
        b"payload",
        model_name=model_name,
    )

    assert result == {"ok": True}
    assert clients[-1].files["file"][2] == "application/octet-stream"


async def test_rate_limit_is_atomic_and_ttl_bounded(redis_client):
    key_id = "phase8-atomic-rate-limit"

    await check_rate_limit(key_id, 2, redis_client)
    ttl = await redis_client.ttl(f"rl:{key_id}")
    assert 0 < ttl <= 60

    await check_rate_limit(key_id, 2, redis_client)
    with pytest.raises(Exception) as exc:
        await check_rate_limit(key_id, 2, redis_client)
    assert getattr(exc.value, "status_code", None) == 429


def _model(version, percent=50, status="active"):
    return ModelRecord(
        id=f"id-{version}",
        name="custom",
        version=version,
        worker_name="custom-worker",
        protocol="http",
        endpoint="http://custom-worker/infer",
        status=status,
        supports_streaming=False,
        canary_percent=percent,
    )


async def test_canary_uses_numeric_highest_and_lowest(monkeypatch, db):
    versions = [_model("v9", 100), _model("v10", 100)]
    monkeypatch.setattr(
        main.registry,
        "list_versions",
        lambda *args, **kwargs: _async_result(versions),
    )
    monkeypatch.setattr(main.random, "randint", lambda start, end: 100)

    result = await main.get_model_with_canary(db, "custom")
    assert result.version == "v10"


async def test_canary_percent_zero_routes_stable(monkeypatch, db):
    versions = [_model("v9", 0), _model("v10", 0)]
    monkeypatch.setattr(
        main.registry,
        "list_versions",
        lambda *args, **kwargs: _async_result(versions),
    )
    monkeypatch.setattr(main.random, "randint", lambda start, end: 100)

    result = await main.get_model_with_canary(db, "custom")
    assert result.version == "v9"


async def test_canary_roll_at_boundary_is_canary(monkeypatch, db):
    versions = [_model("v9", 50), _model("v10", 50)]
    monkeypatch.setattr(
        main.registry,
        "list_versions",
        lambda *args, **kwargs: _async_result(versions),
    )
    monkeypatch.setattr(main.random, "randint", lambda start, end: 50)

    result = await main.get_model_with_canary(db, "custom")
    assert result.version == "v10"


async def test_one_active_canary_version_always_routes_it(monkeypatch, db):
    versions = [_model("v7", 0)]
    monkeypatch.setattr(
        main.registry,
        "list_versions",
        lambda *args, **kwargs: _async_result(versions),
    )

    result = await main.get_model_with_canary(db, "custom")
    assert result.version == "v7"


async def test_explicit_version_bypasses_canary(monkeypatch, client, regular_key):
    calls = []

    async def fake_get_model(db, model_name, version="v1"):
        calls.append((model_name, version))
        return _model(version)

    monkeypatch.setattr(main.registry, "get_model", fake_get_model)

    await client.post(
        "/v1/infer/custom?version=v9",
        headers=auth_header(regular_key),
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert calls[-1] == ("custom", "v9")


async def test_disabled_model_name_returns_503(monkeypatch, db):
    async def no_active_versions(db, model_name):
        return []

    async def existing_disabled(db, model_name, version="v1"):
        return _model("v1", status="disabled")

    monkeypatch.setattr(main.registry, "list_versions", no_active_versions)
    monkeypatch.setattr(main.registry, "get_model", existing_disabled)

    with pytest.raises(main.HTTPException) as exc:
        await main.get_model_with_canary(db, "custom")
    assert exc.value.status_code == 503
    assert "disabled" in str(exc.value.detail).lower()


async def test_ready_returns_503_when_redis_is_down(monkeypatch, client):
    async def ping_down():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(main.redis, "ping", ping_down)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    assert "redis" in resp.json()["detail"].lower()


async def test_ready_returns_200_when_services_are_up(client):
    resp = await client.get("/ready")
    assert resp.status_code == 200


async def test_upload_over_limit_returns_413(client, regular_key):
    resp = await client.post(
        "/v1/infer/doc-ocr",
        headers=auth_header(regular_key),
        files={"file": ("large.jpg", b"x" * (main.MAX_UPLOAD_BYTES + 1), "image/jpeg")},
    )
    assert resp.status_code == 413


async def test_upload_at_limit_is_accepted(monkeypatch, client, regular_key):
    monkeypatch.setattr(
        main,
        "get_model_with_canary",
        lambda db, name: _async_result(_model("v1")),
    )
    monkeypatch.setattr(
        main,
        "enqueue_job",
        lambda redis, model, payload, api_key_id: _async_result("job-limit"),
    )

    resp = await client.post(
        "/v1/infer/doc-ocr",
        headers=auth_header(regular_key),
        files={"file": ("limit.jpg", b"x" * main.MAX_UPLOAD_BYTES, "image/jpeg")},
    )
    assert resp.status_code == 200


async def _async_result(value):
    return value
