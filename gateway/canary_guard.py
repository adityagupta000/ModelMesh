import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class Stats:
    n: int
    err: float


@dataclass
class Config:
    min_requests: int = 30
    error_delta: float = 0.10


def decide(
    stable: Stats, canary: Stats, canary_percent: int, cfg: Config
) -> tuple[str, str]:
    if canary_percent == 0:
        return "noop", "canary already at 0"
    if stable.n < cfg.min_requests:
        return "hold", "not enough stable traffic"
    expected = stable.n * canary_percent / max(1, 100 - canary_percent)
    if expected >= cfg.min_requests and canary.n < 0.2 * expected:
        return (
            "rollback",
            f"canary starved: {canary.n} completed vs about {expected:.0f} expected",
        )
    if canary.n < cfg.min_requests:
        return "hold", "not enough canary traffic"
    if canary.err - stable.err > cfg.error_delta:
        return (
            "rollback",
            f"canary error rate {canary.err:.2f} vs stable {stable.err:.2f}",
        )
    return "hold", "healthy"


def _version_number(version: str) -> int:
    match = re.search(r"\d+", version)
    return int(match.group()) if match else 0


def select_stable_and_canary(
    models: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    active = [model for model in models if model["status"] == "active"]
    if len(active) < 2 or not any(model["canary_percent"] > 0 for model in active):
        return None

    stable = min(active, key=lambda model: _version_number(model["version"]))
    canary = max(active, key=lambda model: _version_number(model["version"]))
    return stable, canary


def query_model_stats(
    model_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    window_min: int,
    host: str,
    port: int,
) -> dict[str, tuple[Stats, Stats]]:
    import clickhouse_connect

    client = clickhouse_connect.get_client(host=host, port=port)
    results: dict[str, tuple[Stats, Stats]] = {}
    query = """
        SELECT model_version, count() AS n,
               countIf(status = 'error') / count() AS err
        FROM request_metrics
        WHERE model_name = {name:String}
          AND timestamp > now() - INTERVAL {window:UInt32} MINUTE
        GROUP BY model_version
    """

    for stable, canary in model_pairs:
        rows = client.query(
            query,
            parameters={"name": canary["name"], "window": window_min},
        ).result_rows
        stats = {
            version: Stats(int(n), float(err))
            for version, n, err in rows
        }
        results[canary["name"]] = (
            stats.get(stable["version"], Stats(0, 0.0)),
            stats.get(canary["version"], Stats(0, 0.0)),
        )
    return results


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if result < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return result


def _error_delta(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate active model canaries.")
    parser.add_argument("--window-min", type=_positive_int, default=10)
    parser.add_argument("--min-requests", type=_positive_int, default=30)
    parser.add_argument("--error-delta", type=_error_delta, default=0.10)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    gateway_url = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
    admin_key = os.getenv("ADMIN_KEY")
    if args.apply and not admin_key:
        parser.error("--apply requires ADMIN_KEY in the environment")

    clickhouse_port = _positive_int(os.getenv("CLICKHOUSE_PORT", "8123"))

    import httpx

    with httpx.Client(timeout=10.0) as http:
        response = http.get(f"{gateway_url}/v1/models")
        response.raise_for_status()
        models = response.json()
        if not isinstance(models, list):
            raise ValueError("GET /v1/models did not return a JSON array")

        grouped: dict[str, list[dict[str, Any]]] = {}
        for model in models:
            grouped.setdefault(model["name"], []).append(model)

        model_pairs = [
            pair
            for name in sorted(grouped)
            if (pair := select_stable_and_canary(grouped[name])) is not None
        ]
        stats_by_model = (
            query_model_stats(
                model_pairs,
                args.window_min,
                os.getenv("CLICKHOUSE_HOST", "clickhouse"),
                clickhouse_port,
            )
            if model_pairs
            else {}
        )

        output = []
        for stable_model, canary_model in model_pairs:
            stable_stats, canary_stats = stats_by_model[canary_model["name"]]
            decision, reason = decide(
                stable_stats,
                canary_stats,
                canary_model["canary_percent"],
                Config(args.min_requests, args.error_delta),
            )
            applied = False
            if args.apply and decision == "rollback":
                patch = http.patch(
                    f"{gateway_url}/v1/models/{canary_model['id']}",
                    json={"canary_percent": 0},
                    headers={"Authorization": f"Bearer {admin_key}"},
                )
                patch.raise_for_status()
                applied = True

            output.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "model": canary_model["name"],
                    "stable": stable_model["version"],
                    "canary": canary_model["version"],
                    "canary_percent": canary_model["canary_percent"],
                    "stable_stats": asdict(stable_stats),
                    "canary_stats": asdict(canary_stats),
                    "decision": decision,
                    "reason": reason,
                    "applied": applied,
                }
            )

    for record in output:
        print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
