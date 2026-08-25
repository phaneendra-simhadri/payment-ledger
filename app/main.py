from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.middleware.auth import auth_required
from app.routers.accounts import router as accounts_router
from app.routers.auth import router as auth_router
from app.routers.deposits import router as deposits_router
from app.routers.transfers import router as transfers_router
from app.routers.withdrawals import router as withdrawals_router
from app.services.rate_limiter import rate_limit_middleware


logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
structlog.configure(processors=[structlog.processors.JSONRenderer()])
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title='Payment Ledger', version='1.0.0', lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Process-Time'] = str(round((time.perf_counter() - start) * 1000, 2))
    logger.info('request_completed', request_id=request_id, status_code=response.status_code)
    return response


@app.middleware('http')
async def rate_limit_middleware_wrapper(request: Request, call_next):
    if request.url.path.startswith('/api/v1/') and request.method in {'POST', 'GET'}:
        return await rate_limit_middleware(request, call_next)
    return await call_next(request)


@app.get('/health')
async def health():
    from app.database import engine
    try:
        async with engine.begin() as conn:
            await conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        from app.services.rate_limiter import redis_client
        if redis_client is not None:
            redis_client.ping()
            redis_ok = True
        else:
            redis_ok = True
    except Exception:
        redis_ok = False

    if not db_ok or not redis_ok:
        return JSONResponse(status_code=503, content={'status': 'error', 'db': 'down' if not db_ok else 'ok', 'redis': 'down' if not redis_ok else 'ok'})
    return {'status': 'ok', 'db': 'ok', 'redis': 'ok'}


@app.get('/api/v1/metrics')
async def metrics():
    from app.services.metrics import get_metrics
    return get_metrics()


@app.get('/api/v1/admin/reconcile')
async def admin_reconcile():
    from app.jobs.reconciliation import run_reconciliation
    from app.database import async_session_maker
    async with async_session_maker() as session:
        await run_reconciliation(session)
    return {'status': 'ok'}


app.include_router(auth_router, prefix='/api/v1')
app.include_router(accounts_router, prefix='/api/v1')
app.include_router(deposits_router, prefix='/api/v1')
app.include_router(withdrawals_router, prefix='/api/v1')
app.include_router(transfers_router, prefix='/api/v1')
