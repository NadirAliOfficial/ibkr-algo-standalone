"""
FastAPI dashboard backend.
Runner and broker are injected at startup by main.py — not created here.
"""

import os
import secrets
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from dashboard.backend.routers import tickers, positions, logs, system

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "changeme")

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


app = FastAPI(
    title="IBKR Algo Dashboard",
    version="2.0.0",
    dependencies=[Depends(auth)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(tickers.router,   prefix="/api/tickers",   tags=["tickers"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(logs.router,      prefix="/api/logs",      tags=["logs"])
app.include_router(system.router,    prefix="/api/system",    tags=["system"])


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
