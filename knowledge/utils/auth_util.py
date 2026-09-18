"""轻量级后端认证工具。"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from fastapi import HTTPException, Query, Request, status

from knowledge.core.paths import KNOWLEDGE_ROOT
from knowledge.schema.auth_schema import AuthUser


load_dotenv(os.path.join(KNOWLEDGE_ROOT, ".env"))

DEFAULT_AUTH_USERNAME = "admin"
DEFAULT_AUTH_PASSWORD = "666666"
DEFAULT_AUTH_SECRET = "shopkeeper-brain-dev-secret"
DEFAULT_AUTH_EXPIRES_SECONDS = 7 * 24 * 60 * 60


def _auth_username() -> str:
  return os.getenv("AUTH_USERNAME") or os.getenv("VITE_LOGIN_USERNAME") or DEFAULT_AUTH_USERNAME


def _auth_password() -> str:
  return os.getenv("AUTH_PASSWORD") or os.getenv("VITE_LOGIN_PASSWORD") or DEFAULT_AUTH_PASSWORD


def _auth_secret() -> str:
  return os.getenv("AUTH_TOKEN_SECRET") or os.getenv("AUTH_SECRET") or DEFAULT_AUTH_SECRET


def _auth_expires_seconds() -> int:
  raw = os.getenv("AUTH_EXPIRES_SECONDS", str(DEFAULT_AUTH_EXPIRES_SECONDS))
  try:
    value = int(raw)
    return value if value > 0 else DEFAULT_AUTH_EXPIRES_SECONDS
  except ValueError:
    return DEFAULT_AUTH_EXPIRES_SECONDS


def _b64_encode(raw: bytes) -> str:
  return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64_decode(value: str) -> bytes:
  padding = "=" * (-len(value) % 4)
  return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def _sign(payload_part: str) -> str:
  digest = hmac.new(_auth_secret().encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
  return _b64_encode(digest)


def _create_token(username: str, now_seconds: int, expires_seconds: int) -> str:
  payload = {
    "sub": username,
    "iat": now_seconds,
    "exp": now_seconds + expires_seconds,
  }
  payload_part = _b64_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
  return f"{payload_part}.{_sign(payload_part)}"


def login_user(username: str, password: str, agreed: bool) -> Dict[str, Any]:
  clean_username = (username or "").strip()
  if not clean_username or not password:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请输入账号和密码")

  if not agreed:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先勾选使用协议和须知")

  username_ok = hmac.compare_digest(clean_username, _auth_username())
  password_ok = hmac.compare_digest(password, _auth_password())
  if not username_ok or not password_ok:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码不正确")

  now_seconds = int(time.time())
  expires_seconds = _auth_expires_seconds()
  return {
    "username": clean_username,
    "token": _create_token(clean_username, now_seconds, expires_seconds),
    "login_at": now_seconds * 1000,
    "expires_at": (now_seconds + expires_seconds) * 1000,
  }


def verify_token(token: str) -> AuthUser:
  if not token or "." not in token:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态无效，请重新登录")

  payload_part, signature = token.rsplit(".", 1)
  expected = _sign(payload_part)
  if not hmac.compare_digest(signature, expected):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态无效，请重新登录")

  try:
    payload = json.loads(_b64_decode(payload_part).decode("utf-8"))
  except Exception as exc:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态无效，请重新登录") from exc

  username = str(payload.get("sub") or "")
  exp = int(payload.get("exp") or 0)
  if not username or exp <= int(time.time()):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录状态已过期，请重新登录")

  return AuthUser(username=username)


def _extract_bearer_token(request: Request) -> Optional[str]:
  auth_header = request.headers.get("Authorization", "")
  prefix = "Bearer "
  if auth_header.startswith(prefix):
    return auth_header[len(prefix):].strip()
  return None


def require_auth_user(
  request: Request,
  access_token: Optional[str] = Query(default=None, description="SSE 等无法设置请求头时使用的访问令牌"),
) -> AuthUser:
  token = access_token or _extract_bearer_token(request)
  if not token:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
  return verify_token(token)
