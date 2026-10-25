"""Settings from the environment. Defaults suit running on the host against docker compose."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APP_DSN = os.environ.get("DATABASE_URL", "postgresql://sc_app:app_pw@localhost:5433/safecounter")
OWNER_DSN = os.environ.get("OWNER_DATABASE_URL", "postgresql://sc_owner:owner_pw@localhost:5433/safecounter")
APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD", "app_pw")
DELIVERY_DB_PASSWORD = os.environ.get("DELIVERY_DB_PASSWORD", "delivery_pw")

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

# Which AI answers (D23). The mock is the default and needs no key.
AI_PROVIDER = os.environ.get("AI_PROVIDER", "mock")             # "mock" or "groq"
AI_PROMPT_VARIANT = os.environ.get("AI_PROMPT_VARIANT", "few")  # "few" (with examples) or "zero"
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")               # from the local .env only; never logged
GROQ_DEADLINE_SECONDS = float(os.environ.get("GROQ_DEADLINE_SECONDS", "30"))

LOW_CONFIDENCE = 0.7
