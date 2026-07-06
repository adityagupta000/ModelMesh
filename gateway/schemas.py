from pydantic import BaseModel, EmailStr
from typing import Optional


class UserCreate(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreate(BaseModel):
    rate_limit_per_min: int = 60


class ApiKeyResponse(BaseModel):
    key: str
    rate_limit_per_min: int


class ModelRegistration(BaseModel):
    name: str
    version: str = "v1"
    worker_name: str
    protocol: str
    endpoint: str
    supports_streaming: bool = False
    description: Optional[str] = None


class ModelUpdate(BaseModel):
    worker_name: Optional[str] = None
    protocol: Optional[str] = None
    endpoint: Optional[str] = None
    status: Optional[str] = None
    supports_streaming: Optional[bool] = None
    description: Optional[str] = None


class InferResponse(BaseModel):
    result: dict
    model: str
    version: str
    latency_ms: int
    cached: bool = False
