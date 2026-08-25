from __future__ import annotations

from collections import defaultdict

_METRICS = {
    'transfers_total': 0,
    'deposits_total': 0,
    'withdrawals_total': 0,
    'failed_transactions_total': 0,
    'webhook_dead_letter_total': 0,
    'p99_transfer_latency_ms': 0,
}


def increment(metric: str, value: int = 1) -> None:
    _METRICS[metric] = _METRICS.get(metric, 0) + value


def get_metrics() -> dict:
    return dict(_METRICS)
