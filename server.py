#!/usr/bin/env python3
"""OpenClaw Authorization Gateway — entry point."""

import logging

import uvicorn

from gateway.app import app  # noqa: F401 — needed for uvicorn import path
from gateway.config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=CONFIG.get("port", 18795),
        log_level="info",
    )
