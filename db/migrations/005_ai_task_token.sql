-- Level 2F: each AI task gets one narrow, single-use token (D27).
-- Only its SHA-256 is stored; the plain token goes to the AI process once.
ALTER TABLE ai_tasks ADD COLUMN token_sha256 text UNIQUE;
ALTER TABLE ai_tasks ADD COLUMN token_expires_at timestamptz;
