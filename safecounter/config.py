"""Settings from the environment. Defaults suit running on the host against docker compose."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APP_DSN = os.environ.get("DATABASE_URL", "postgresql://sc_app:app_pw@localhost:5433/safecounter")
OWNER_DSN = os.environ.get("OWNER_DATABASE_URL", "postgresql://sc_owner:owner_pw@localhost:5433/safecounter")
APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD", "app_pw")

RULES_PATH = Path(os.environ.get("RULES_PATH", ROOT / "data" / "roles_and_fields.json"))
SEED_PATH = ROOT / "data" / "seed_records.json"
NOTES_PATH = ROOT / "data" / "notes.json"
AI_SCHEMA_PATH = ROOT / "mock_ai" / "proposal_schema.json"
PROMPT_PATH = ROOT / "prompts" / "scribe_v1.md"

AI_URL = os.environ.get("AI_URL", "http://localhost:8099/v1/propose")
AI_DEADLINE_SECONDS = float(os.environ.get("AI_DEADLINE_SECONDS", "5"))
AI_MAX_RESPONSE_BYTES = int(os.environ.get("AI_MAX_RESPONSE_BYTES", str(256 * 1024)))
# Test hook only: lets a caller pick the mock's mode per request (X-AI-Mode header).
ALLOW_AI_MODE_OVERRIDE = os.environ.get("ALLOW_AI_MODE_OVERRIDE", "0") == "1"

LOW_CONFIDENCE = 0.7
