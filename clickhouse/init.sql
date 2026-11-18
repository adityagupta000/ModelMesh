-- RECONSTRUCTED from the insert columns and documented queries. Verify with SHOW CREATE TABLE.
CREATE TABLE IF NOT EXISTS request_metrics (
    timestamp     DateTime DEFAULT now(),
    request_id    String,
    model_id      String,
    model_name    String,
    model_version String,
    worker_name   String,
    status        String,
    latency_ms    UInt32
) ENGINE = MergeTree
ORDER BY (model_name, model_version, timestamp);
