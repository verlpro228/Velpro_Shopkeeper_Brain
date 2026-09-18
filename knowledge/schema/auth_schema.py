"""认证相关 Schema。"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
  username: str = Field(..., description="账号")
  password: str = Field(..., description="密码")
  agreed: bool = Field(False, description="是否同意使用协议和须知")


class LoginResponse(BaseModel):
  message: str = Field("登录成功", description="响应消息")
  username: str = Field(..., description="账号")
  token: str = Field(..., description="访问令牌")
  token_type: str = Field("bearer", description="令牌类型")
  login_at: int = Field(..., description="登录时间，毫秒时间戳")
  expires_at: int = Field(..., description="过期时间，毫秒时间戳")


class AuthUser(BaseModel):
  username: str = Field(..., description="当前登录账号")
