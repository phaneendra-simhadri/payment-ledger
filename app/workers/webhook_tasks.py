import hashlib
import hmac
import json
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from celery import Task
from sqlalchemy import select

from app.config import settings
from app.database import async_session_maker
from app.models import WebhookDelivery
from app.workers.celery_app import celery_app

WEBHOOK_SECRET = settings.webhook_secret
RETRY_DELAYS = [10, 30, 120, 600, 3600]


def sign_payload(payload: str) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def _deliver(webhook_delivery_id: str) -> dict:
    async with async_session_maker() as session:
        delivery = await session.scalar(select(WebhookDelivery).where(WebhookDelivery.id == webhook_delivery_id))
        if delivery is None:
            return {'status': 'missing'}
        payload_str = json.dumps(delivery.payload, sort_keys=True, separators=(',', ':'))
        delivery.attempts += 1
        delivery.last_attempted_at = datetime.now(timezone.utc)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    delivery.target_url,
                    content=payload_str,
                    headers={
                        'Content-Type': 'application/json',
                        'X-Signature': f'sha256={sign_payload(payload_str)}',
                        'X-Delivery-Id': str(delivery.id),
                    },
                )
                response.raise_for_status()
            delivery.status = 'delivered'
            delivery.next_retry_at = None
            await session.commit()
            return {'status': 'delivered'}
        except Exception:
            if delivery.attempts >= len(RETRY_DELAYS):
                delivery.status = 'dead'
                delivery.next_retry_at = None
            else:
                delivery.status = 'failed'
                delivery.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAYS[delivery.attempts - 1])
            await session.commit()
            raise


@celery_app.task(bind=True, max_retries=5, name='deliver_webhook')
def deliver_webhook(self: Task, webhook_delivery_id: str):
    try:
        return asyncio.run(_deliver(webhook_delivery_id))
    except Exception as exc:
        attempt = self.request.retries
        if attempt < len(RETRY_DELAYS) - 1:
            raise self.retry(exc=exc, countdown=RETRY_DELAYS[attempt])
        return {'status': 'dead'}
