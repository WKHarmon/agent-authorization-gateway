"""Configuration loading and constants."""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("gateway")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DATA_DIR = BASE_DIR / "data"
AUDIT_LOG_PATH = DATA_DIR / "audit.jsonl"
GRANTS_DB_PATH = DATA_DIR / "grants.db"

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_ROLE_ID = os.environ.get("VAULT_ROLE_ID", "")
VAULT_SECRET_ID = os.environ.get("VAULT_SECRET_ID", "")
VAULT_ENABLED = bool(VAULT_ROLE_ID and VAULT_SECRET_ID)

MAX_GRANT_DURATION_MINUTES = 1440  # 24 hours


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_sensitive_patterns(config: dict) -> dict:
    path = BASE_DIR / config.get("sensitive_patterns_file", "sensitive_patterns.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"redact_subjects": [], "redact_senders": []}


CONFIG = load_config()
SENSITIVE = load_sensitive_patterns(CONFIG)
