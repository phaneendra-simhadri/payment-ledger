from celery import Celery

from app.config import settings

celery_app = Celery(
    'payment_ledger',
    broker=settings.redis_url,
    backend=settings.redis_url.replace('/0', '/1'),
    include=['app.workers.webhook_tasks', 'app.jobs.reconciliation'],
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        'reconcile-ledger-hourly': {
            'task': 'run_reconciliation_task',
            'schedule': 60 * 60,
        },
    },
)
