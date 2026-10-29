"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api import alerts, baskets, ledger, matches, ws

app = FastAPI(title="Kirana Kare", version="0.1.0")
app.include_router(alerts.router)
app.include_router(ledger.router)
app.include_router(matches.router)
app.include_router(baskets.router)
app.include_router(ws.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
