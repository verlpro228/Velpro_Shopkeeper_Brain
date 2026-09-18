"""认证路由注册。"""

from fastapi import Depends, FastAPI

from knowledge.schema.auth_schema import AuthUser, LoginRequest, LoginResponse
from knowledge.utils.auth_util import login_user, require_auth_user


def register_auth_routes(app: FastAPI) -> None:
  @app.post("/auth/login", response_model=LoginResponse)
  async def login(request: LoginRequest):
    session = login_user(request.username, request.password, request.agreed)
    return LoginResponse(**session)

  @app.get("/auth/me", response_model=AuthUser)
  async def me(current_user: AuthUser = Depends(require_auth_user)):
    return current_user
