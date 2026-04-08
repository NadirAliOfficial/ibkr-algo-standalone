"""
FastAPI dashboard backend.
Runner and broker are injected at startup by main.py — not created here.
"""

import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dashboard.backend.routers import tickers, positions, logs, system

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "changeme")

FRONTEND_BUILD = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "build"
)

def auth(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    import base64
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid header")
    ok = (
        secrets.compare_digest(username, DASHBOARD_USER)
        and secrets.compare_digest(password, DASHBOARD_PASS)
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return username


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


@app.get("/api/auth/verify", include_in_schema=False, dependencies=[Depends(auth)])
def verify_auth():
    return {"ok": True}


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
