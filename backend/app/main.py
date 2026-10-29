"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(title="Kirana Kare", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
