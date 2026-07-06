-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID REFERENCES users(id) ON DELETE CASCADE,
    key_hash           TEXT UNIQUE NOT NULL,
    rate_limit_per_min INT DEFAULT 60,
    is_admin           BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMPTZ DEFAULT now(),
    revoked            BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS models (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  version TEXT NOT NULL DEFAULT 'v1',
  worker_name TEXT NOT NULL,
  protocol TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  supports_streaming BOOLEAN NOT NULL DEFAULT FALSE,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS requests (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id  UUID REFERENCES api_keys(id),
    model_id    UUID REFERENCES models(id),
    model_type  TEXT NOT NULL,
    status      TEXT NOT NULL,            -- 'success' | 'error' | 'rate_limited'
    latency_ms  INT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Seed the two initial models
INSERT INTO models (name, version, worker_name, protocol, endpoint, supports_streaming, description)
VALUES
('doc-ocr', 'v1', 'doc-ocr-worker', 'http', 'http://doc-ocr-worker:8001/infer', FALSE, 'EasyOCR document text extraction'),
('asr', 'v1', 'asr-worker', 'http', 'http://asr-worker:8002/infer', TRUE, 'Whisper-tiny speech-to-text')
ON CONFLICT (name, version) DO NOTHING;