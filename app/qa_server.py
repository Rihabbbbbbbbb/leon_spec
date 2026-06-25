"""
Standalone entry point for the Spec Q&A Assistant.

Runs a small FastAPI server that:
- serves the chat UI at /
- exposes the Q&A API under /api

Run:
    python -m app.qa_server
or:
    uvicorn app.qa_server:app --reload --port 8010
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.qa.route import router as qa_router

UI_DIR = Path(__file__).resolve().parent / "qa_ui"

app = FastAPI(title="Spec Q&A Assistant", version="1.0.0")

# API routes
app.include_router(qa_router)


@app.get("/")
def serve_ui() -> FileResponse:
    """Serve the chat UI."""
    return FileResponse(UI_DIR / "index.html")


# Health check
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "spec-qa-assistant"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.qa_server:app", host="0.0.0.0", port=8010, reload=True)
