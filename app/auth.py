from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer = HTTPBearer()


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    if credentials.credentials != settings.api_auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid or missing API token", "code": "AUTH_FAILED"},
            headers={"WWW-Authenticate": "Bearer"},
        )
