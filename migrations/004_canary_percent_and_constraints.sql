-- Idempotent: safe on a DB where canary_percent was added by hand.
ALTER TABLE models ADD COLUMN IF NOT EXISTS canary_percent INT;
UPDATE models SET canary_percent = 0 WHERE canary_percent IS NULL;
ALTER TABLE models ALTER COLUMN canary_percent SET DEFAULT 0;
ALTER TABLE models ALTER COLUMN canary_percent SET NOT NULL;

ALTER TABLE models DROP CONSTRAINT IF EXISTS models_protocol_chk;
ALTER TABLE models ADD CONSTRAINT models_protocol_chk CHECK (protocol IN ('http','grpc'));
ALTER TABLE models DROP CONSTRAINT IF EXISTS models_status_chk;
ALTER TABLE models ADD CONSTRAINT models_status_chk CHECK (status IN ('active','disabled','deleted'));
ALTER TABLE models DROP CONSTRAINT IF EXISTS models_canary_chk;
ALTER TABLE models ADD CONSTRAINT models_canary_chk CHECK (canary_percent BETWEEN 0 AND 100);
