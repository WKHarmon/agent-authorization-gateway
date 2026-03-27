"""Health check endpoint."""

from fastapi import FastAPI


def register(app: FastAPI):
    @app.get("/health")
    async def health():
        return {"status": "ok"}
