import logging
from clickhouse_connect import get_client

logger = logging.getLogger(__name__)

_ch_client = None


def get_ch_client():
    global _ch_client
    if _ch_client is None:
        _ch_client = get_client(host="clickhouse", port=8123)
    return _ch_client


async def log_metric(request_id: str, model, status: str, latency_ms: int):
    """Fire-and-forget metric write. Never let a ClickHouse failure break the request."""
    try:
        client = get_ch_client()
        client.insert(
            "request_metrics",
            [[request_id, model.id, model.name, model.version, model.worker_name, status, latency_ms]],
            column_names=["request_id", "model_id", "model_name", "model_version", "worker_name", "status", "latency_ms"],
        )
    except Exception as e:
        # Metrics are best-effort — a ClickHouse outage should never break inference.
        logger.warning(f"Failed to log metric to ClickHouse: {e}")