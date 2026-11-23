ALTER TABLE models
ADD COLUMN IF NOT EXISTS supports_streaming BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE models
SET supports_streaming = TRUE
WHERE name = 'asr'
AND version = 'v1';