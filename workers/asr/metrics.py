import logging
import os
import time

log = logging.getLogger(__name__)

_client = None
_skip_until = 0.0


def _get_client():
    global _client
    if _client is None:
        import clickhouse_connect

        _client = clickhouse_connect.get_client(
            host=os.environ["CLICKHOUSE_HOST"],
            port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
            connect_timeout=2,
            send_receive_timeout=5,
        )
    return _client


def log_metric(
    request_id: str,
    model_id: str,
    model_name: str,
    model_version: str,
    worker_name: str,
    status: str,
    latency_ms: int,
):
    """Write one metric row without surfacing ClickHouse failures to workers."""
    global _client, _skip_until
    if not os.getenv("CLICKHOUSE_HOST"):
        return
    now = time.monotonic()
    if now < _skip_until:
        return
    try:
        _get_client().insert(
            "request_metrics",
            [[
                request_id,
                model_id,
                model_name,
                model_version,
                worker_name,
                status,
                int(latency_ms),
            ]],
            column_names=[
                "request_id",
                "model_id",
                "model_name",
                "model_version",
                "worker_name",
                "status",
                "latency_ms",
            ],
        )
    except Exception as exc:
        _client = None
        _skip_until = time.monotonic() + 60.0
        log.warning("metric insert failed (skipping for 60s): %s", exc)
