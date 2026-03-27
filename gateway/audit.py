"""Append-only JSONL audit log."""

import json
import logging
from datetime import datetime, timezone

from gateway.config import AUDIT_LOG_PATH, DATA_DIR

log = logging.getLogger("gateway.audit")


def audit(entry: dict):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    log.info("AUDIT %s", entry.get("action", "unknown"))
