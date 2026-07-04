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
        description=row.get("description"),
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
        description=row.get("description"),
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
            description=r.get("description"),
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
    description: Optional[str] = None,
) -> str:
    row = await db.fetch_one(
        """INSERT INTO models (name, version, worker_name, protocol, endpoint, description)
           VALUES (:name, :version, :worker_name, :protocol, :endpoint, :description)
           RETURNING id""",
        {
            "name": name,
            "version": version,
            "worker_name": worker_name,
            "protocol": protocol,
            "endpoint": endpoint,
            "description": description,
        },
    )
    return str(row["id"])


async def update_model(db: Database, model_id: str, **fields) -> None:
    if not fields:
        return
    allowed = {"worker_name", "protocol", "endpoint", "status", "description"}
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
