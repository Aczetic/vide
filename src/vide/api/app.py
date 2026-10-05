"""FastAPI application.

Serves the a later stage UI. Generation-triggering endpoints are deliberately absent until
a later stage, and when they land they carry the role check requires — a client must
never be able to reach them.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vide.api.routes import router

app = FastAPI(
    title="vide",
    description="Stage 1 pre-production platform",
    version="0.1.0",
)

# Local development only: the Next.js dev server runs on a different port.
# A deployment serves the UI from the same origin and does not need this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
