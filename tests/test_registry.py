import secrets

import pytest
import registry
from conftest import auth_header

pytestmark = pytest.mark.asyncio(scope="session")


async def test_lookup_known_model(db):
    model = await registry.get_model(db, "doc-ocr")
    assert model is not None
    assert model.status == "active"
    assert model.protocol == "http"


async def test_lookup_unknown_model(db):
    model = await registry.get_model(db, "does-not-exist")
    assert model is None


async def test_list_models_returns_seeded(db):
    models = await registry.list_models(db)
    names = [m.name for m in models]
    assert "doc-ocr" in names
    assert "asr" in names


async def test_disabled_model_returns_503(client, db, regular_key, doc_ocr_model_id):
    model = await registry.get_model(db, "doc-ocr")
    assert model is not None
    try:
        await registry.disable_model(db, doc_ocr_model_id)
        resp = await client.post(
            "/v1/infer/doc-ocr",
            headers=auth_header(regular_key),
            files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
        )
        assert resp.status_code == 503
    finally:
        await registry.update_model(db, doc_ocr_model_id, status=model.status)


async def test_unknown_model_returns_404(client, regular_key):
    resp = await client.post(
        "/v1/infer/no-such-model",
        headers=auth_header(regular_key),
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert resp.status_code == 404


async def test_duplicate_registration_rejected(db, admin_key, client):
    resp = await client.post(
        "/v1/models",
        json={
            "name": "doc-ocr",
            "version": "v1",
            "worker_name": "doc-ocr-worker",
            "protocol": "http",
            "endpoint": "http://doc-ocr-worker:8001/infer",
        },
        headers=auth_header(admin_key),
    )
    assert resp.status_code == 409


async def test_admin_only_can_register(client, regular_key):
    resp = await client.post(
        "/v1/models",
        json={
            "name": "ocr-v2",
            "version": "v1",
            "worker_name": "ocr-v2-worker",
            "protocol": "http",
            "endpoint": "http://ocr-v2-worker:8003/infer",
        },
        headers=auth_header(regular_key),
    )
    assert resp.status_code == 403


async def test_register_and_disable_new_model(client, db, admin_key):
    model_name = f"test-ocr-{secrets.token_hex(4)}"
    resp = await client.post(
        "/v1/models",
        json={
            "name": model_name,
            "version": "v1",
            "worker_name": "test-ocr-worker",
            "protocol": "http",
            "endpoint": "http://test-ocr-worker:8003/infer",
            "description": "Test OCR model",
        },
        headers=auth_header(admin_key),
    )
    assert resp.status_code == 200
    model_id = resp.json()["id"]

    # Disable it
    resp = await client.delete(f"/v1/models/{model_id}", headers=auth_header(admin_key))
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


async def test_get_model_endpoint(client):
    resp = await client.get("/v1/models/doc-ocr")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "doc-ocr"


async def test_get_unknown_model_endpoint(client):
    resp = await client.get("/v1/models/nonexistent")
    assert resp.status_code == 404
