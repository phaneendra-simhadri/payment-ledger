from __future__ import annotations

import asyncio
import time
from typing import Any

import redis
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from starlette.responses import JSONResponse

from app.config import settings

redis_client = None
try:
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    redis_client.ping()
except Exception:
    redis_client = None

memory_buckets: dict[str, dict[str, Any]] = {}


def _bucket_key(user_id: str, kind: str) -> str:
    return f'{user_id}:{kind}'


async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith('/api/v1/'):
        return await call_next(request)

    user_id = getattr(request.state, 'user_id', None)
    if user_id is None:
        authorization = request.headers.get('Authorization', '')
        if authorization.lower().startswith('bearer '):
            try:
                claims = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=['HS256'])
                user_id = claims.get('user_id')
            except JWTError:
                user_id = None
    if user_id is None:
        return await call_next(request)

    kind = 'mutation' if request.method in {'POST', 'PUT', 'DELETE', 'PATCH'} else 'read'
    limit = 10 if kind == 'mutation' else 60
    window = 1 if kind == 'mutation' else 60

    key = _bucket_key(str(user_id), kind)
    now = time.time()
    if redis_client is not None:
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        count, _ = pipe.execute()
        if int(count) > limit:
            retry_after = max(1, int(window - (now % window)))
            return JSONResponse(status_code=429, headers={'Retry-After': str(retry_after)}, content={'error': 'RATE_LIMITED', 'message': 'Too many requests'})
        return await call_next(request)

    bucket = memory_buckets.setdefault(key, {'count': 0, 'window_start': now})
    if now - bucket['window_start'] > window:
        bucket['count'] = 0
        bucket['window_start'] = now
    bucket['count'] += 1
    if bucket['count'] > limit:
        retry_after = max(1, int(window - (now - bucket['window_start'])))
        return JSONResponse(status_code=429, headers={'Retry-After': str(retry_after)}, content={'error': 'RATE_LIMITED', 'message': 'Too many requests'})
    return await call_next(request)
