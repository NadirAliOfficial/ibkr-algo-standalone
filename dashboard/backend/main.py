"""
FastAPI dashboard backend.
Serves config read/write, live positions, trade logs, signal logs, system status.
"""

import os
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config.store import ConfigStore
from dashboard.backend.routers import tickers, positions, logs, system

DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "changeme")

security = HTTPBasic()
store = ConfigStore()


def auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, DASHBOARD_USER)
    ok_pass = secrets.compare_digest(credentials.password, DASHBOARD_PASS)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


app = FastAPI(title="IBKR Algo Dashboard", version="1.0.0", dependencies=[Depends(auth)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tickers.router, prefix="/api/tickers", tags=["tickers"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(system.router, prefix="/api/system", tags=["system"])


@app.get("/health")
def health():
    return {"status": "ok"}
