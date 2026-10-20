from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.models import User

security = HTTPBearer(auto_error=False)


def create_jwt(user_id: str) -> str:
    payload = {'user_id': user_id, 'exp': datetime.now(timezone.utc) + timedelta(hours=24)}
    return jwt.encode(payload, settings.jwt_secret, algorithm='HS256')


async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> User:
    if credentials is None or not credentials.scheme.lower() == 'bearer':
        raise HTTPException(status_code=401, detail={'error': 'UNAUTHORIZED', 'message': 'Missing or invalid authorization header'})

    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=['HS256'])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail={'error': 'UNAUTHORIZED', 'message': 'Invalid token'}) from exc

    user_id = payload.get('user_id')
    if not user_id:
        raise HTTPException(status_code=401, detail={'error': 'UNAUTHORIZED', 'message': 'Invalid token'})

    try:
        user_uuid = UUID(str(user_id))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail={'error': 'UNAUTHORIZED', 'message': 'Invalid token'}) from exc

    async with async_session_maker() as session:
        user = await session.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=401, detail={'error': 'UNAUTHORIZED', 'message': 'User not found'})
    request.state.user = user
    return user


async def auth_required(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    return await get_current_user(request, credentials)
