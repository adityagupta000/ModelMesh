from dataclasses import dataclass
from typing import Optional
from databases import Database


@dataclass
class ModelRecord:
    id: str
    name: str
    version: str
    worker_name: str
    protocol: str
    endpoint: str
    status: str
    supports_streaming: bool = False
    canary_percent: int = 0
    description: Optional[str] = None


async def get_model(db: Database, model_name: str, version: str = "v1") -> Optional[ModelRecord]:
    row = await db.fetch_one(
        "SELECT * FROM models WHERE name = :name AND version = :version AND status != 'deleted'",
        {"name": model_name, "version": version},
    )
    if not row:
        return None
    return ModelRecord(
        id=str(row["id"]),
        name=row["name"],
        version=row["version"],
        worker_name=row["worker_name"],
        protocol=row["protocol"],
        endpoint=row["endpoint"],
        status=row["status"],
        supports_streaming=row["supports_streaming"],
        canary_percent=row["canary_percent"],
        description=row["description"],
    )


async def get_model_by_id(db: Database, model_id: str) -> Optional[ModelRecord]:
    row = await db.fetch_one(
        "SELECT * FROM models WHERE id = :id",
        {"id": model_id},
    )
    if not row:
        return None
    return ModelRecord(
        id=str(row["id"]),
        name=row["name"],
        version=row["version"],
        worker_name=row["worker_name"],
        protocol=row["protocol"],
        endpoint=row["endpoint"],
        status=row["status"],
        supports_streaming=row["supports_streaming"],
        canary_percent=row["canary_percent"],
        description=row["description"],
    )


async def list_models(db: Database) -> list[ModelRecord]:
    rows = await db.fetch_all(
        "SELECT * FROM models WHERE status != 'deleted' ORDER BY name, version"
    )
    return [
        ModelRecord(
            id=str(r["id"]),
            name=r["name"],
            version=r["version"],
            worker_name=r["worker_name"],
            protocol=r["protocol"],
            endpoint=r["endpoint"],
            status=r["status"],
            supports_streaming=r["supports_streaming"],
            canary_percent=r["canary_percent"],
            description=r["description"],
        )
        for r in rows
    ]


async def list_versions(db: Database, model_name: str) -> list[ModelRecord]:
    """All active versions of a model, ordered by version string.
    Used by canary routing to find the 'stable' (lowest) and 'canary' (highest) versions."""
    rows = await db.fetch_all(
        "SELECT * FROM models WHERE name = :name AND status = 'active' ORDER BY version",
        {"name": model_name},
    )
    return [
        ModelRecord(
            id=str(r["id"]),
            name=r["name"],
            version=r["version"],
            worker_name=r["worker_name"],
            protocol=r["protocol"],
            endpoint=r["endpoint"],
            status=r["status"],
            supports_streaming=r["supports_streaming"],
            canary_percent=r["canary_percent"],
            description=r["description"],
        )
        for r in rows
    ]


async def register_model(
    db: Database,
    name: str,
    version: str,
    worker_name: str,
    protocol: str,
    endpoint: str,
    supports_streaming: bool = False,
    canary_percent: int = 0,
    description: Optional[str] = None,
) -> str:
    row = await db.fetch_one(
        """INSERT INTO models (name, version, worker_name, protocol, endpoint, supports_streaming, canary_percent, description)
           VALUES (:name, :version, :worker_name, :protocol, :endpoint, :supports_streaming, :canary_percent, :description)
           RETURNING id""",
        {
            "name": name,
            "version": version,
            "worker_name": worker_name,
            "protocol": protocol,
            "endpoint": endpoint,
            "supports_streaming": supports_streaming,
            "canary_percent": canary_percent,
            "description": description,
        },
    )
    return str(row["id"])


async def update_model(db: Database, model_id: str, **fields) -> None:
    if not fields:
        return
    allowed = {"worker_name", "protocol", "endpoint", "status", "supports_streaming", "canary_percent", "description"}
    safe_fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not safe_fields:
        return
    set_clause = ", ".join(f"{k} = :{k}" for k in safe_fields)
    safe_fields["model_id"] = model_id
    await db.execute(
        f"UPDATE models SET {set_clause} WHERE id = :model_id",
        safe_fields,
    )


async def disable_model(db: Database, model_id: str) -> None:
    await db.execute(
        "UPDATE models SET status = 'disabled' WHERE id = :id",
        {"id": model_id},
    )