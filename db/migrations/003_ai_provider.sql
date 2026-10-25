-- Which AI produced each task's output (D23). Never holds credentials.
ALTER TABLE ai_tasks ADD COLUMN provider text NOT NULL DEFAULT 'mock';
ALTER TABLE ai_tasks ADD COLUMN model    text NOT NULL DEFAULT 'mock';
ALTER TABLE ai_tasks ADD COLUMN ai_meta  jsonb NOT NULL DEFAULT '{}';  -- usage, request id, finish reason

GRANT UPDATE (ai_meta) ON ai_tasks TO sc_app;
