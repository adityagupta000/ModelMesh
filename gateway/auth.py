import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from databases import Database
from fastapi import Depends, HTTPException, Header
from dataclasses import dataclass

SECRET_KEY = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours


@dataclass
class ApiKeyRecord:
    id: str
    user_id: str
    rate_limit_per_min: int
    is_admin: bool = False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(
    db: Database, user_id: str, rate_limit_per_min: int = 60, is_admin: bool = False
) -> str:
    raw_key = secrets.token_urlsafe(32)
    key_hash = _hash_key(raw_key)
    await db.execute(
        """INSERT INTO api_keys (user_id, key_hash, rate_limit_per_min, is_admin)
           VALUES (:user_id, :key_hash, :rate_limit_per_min, :is_admin)""",
        {
            "user_id": user_id,
            "key_hash": key_hash,
            "rate_limit_per_min": rate_limit_per_min,
            "is_admin": is_admin,
        },
    )
    return raw_key


async def _lookup_key(db: Database, raw_key: str) -> Optional[ApiKeyRecord]:
    key_hash = _hash_key(raw_key)
    row = await db.fetch_one(
        "SELECT id, user_id, rate_limit_per_min, is_admin FROM api_keys WHERE key_hash = :h AND revoked = FALSE",
        {"h": key_hash},
    )
    if not row:
        return None
    return ApiKeyRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        rate_limit_per_min=row["rate_limit_per_min"],
        is_admin=row["is_admin"],
    )


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    return authorization.removeprefix("Bearer ").strip()


def make_verify_api_key(db: Database):
    async def verify_api_key(authorization: Optional[str] = Header(None)) -> ApiKeyRecord:
        raw_key = _extract_bearer(authorization)
        record = await _lookup_key(db, raw_key)
        if not record:
            raise HTTPException(401, "Invalid or revoked API key")
        return record

    return verify_api_key


def make_verify_admin_key(db: Database):
    async def verify_admin_key(authorization: Optional[str] = Header(None)) -> ApiKeyRecord:
        raw_key = _extract_bearer(authorization)
        record = await _lookup_key(db, raw_key)
        if not record:
            raise HTTPException(401, "Invalid or revoked API key")
        if not record.is_admin:
            raise HTTPException(403, "Admin key required")
        return record

    return verify_admin_key
