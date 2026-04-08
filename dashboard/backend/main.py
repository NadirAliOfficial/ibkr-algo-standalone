"""
FastAPI dashboard backend.
Runner and broker are injected at startup by main.py — not created here.
"""

import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dashboard.backend.routers import tickers, positions, logs, system

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "changeme")

FRONTEND_BUILD = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "build"
)

security = HTTPBasic()


def auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok = (
        secrets.compare_digest(credentials.username, DASHBOARD_USER)
        and secrets.compare_digest(credentials.password, DASHBOARD_PASS)
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


app = FastAPI(title="IBKR Algo Dashboard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

_auth = [Depends(auth)]

app.include_router(tickers.router,   prefix="/api/tickers",   tags=["tickers"],   dependencies=_auth)
app.include_router(positions.router, prefix="/api/positions", tags=["positions"], dependencies=_auth)
app.include_router(logs.router,      prefix="/api/logs",      tags=["logs"],      dependencies=_auth)
app.include_router(system.router,    prefix="/api/system",    tags=["system"],    dependencies=_auth)


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# Serve React build if it exists — must be last
if os.path.isdir(FRONTEND_BUILD):
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(FRONTEND_BUILD, "static")),
        name="static-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str):
        return FileResponse(os.path.join(FRONTEND_BUILD, "index.html"))
