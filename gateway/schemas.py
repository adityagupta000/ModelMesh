from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ApiKeyCreate(BaseModel):
    rate_limit_per_min: int = Field(default=60, ge=1, le=1000)


class ApiKeyResponse(BaseModel):
    key: str
    rate_limit_per_min: int


class ModelRegistration(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(default="v1", pattern=r"^v\d+$")
    worker_name: str
    protocol: Literal["http", "grpc"]
    endpoint: str
    supports_streaming: bool = False
    canary_percent: int = Field(default=0, ge=0, le=100)
    description: Optional[str] = None


class ModelUpdate(BaseModel):
    worker_name: Optional[str] = None
    protocol: Optional[Literal["http", "grpc"]] = None
    endpoint: Optional[str] = None
    status: Optional[Literal["active", "disabled"]] = None
    supports_streaming: Optional[bool] = None
    canary_percent: Optional[int] = Field(default=None, ge=0, le=100)
    description: Optional[str] = None
