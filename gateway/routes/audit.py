"""Audit log endpoint."""

import json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from gateway.config import AUDIT_LOG_PATH


def register(app: FastAPI):

    @app.get("/api/audit")
    async def get_audit(
        since: Optional[str] = None,
        limit: int = Query(default=50, le=500),
    ):
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except ValueError:
                raise HTTPException(400, "Invalid 'since' timestamp format")

        entries: list[dict] = []
        if AUDIT_LOG_PATH.exists():
            with open(AUDIT_LOG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if since_dt:
                        try:
                            entry_dt = datetime.fromisoformat(entry.get("ts", ""))
                            if entry_dt <= since_dt:
                                continue
                        except (ValueError, TypeError):
                            continue
                    entries.append(entry)
        entries.reverse()
        return {"entries": entries[:limit]}
