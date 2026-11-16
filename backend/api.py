"""Loopback HTTP query API. MQTT ingestion runs in a separate process."""
import sqlite3
import secrets
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .database import Database

DeviceFilter = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$", max_length=64)]


class Activity(BaseModel):
    id: int
    device_id: str
    boot_id: str
    sequence_end: int
    activity: Literal["walking", "running", "stationary"]
    walking_probability: float
    running_probability: float
    stationary_probability: float = 0.0
    prediction_schema_version: int = 1
    window_start_ms: int
    device_timestamp_ms: int
    server_timestamp: str
    model_id: str
    inference_mode: Literal["dataset_demo", "board_validated"]
    hardware_validated: bool


class History(BaseModel):
    items: list[Activity]
    limit: int
    next_before_id: int | None


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(database=None, settings=None):
    settings = settings or Settings.from_env()
    database = database or Database(settings.database_path)
    production = settings.environment == "production"
    app = FastAPI(title="BCA182 Activity API", version="0.2.0",
                  docs_url=None if production else "/docs", redoc_url=None if production else "/redoc",
                  openapi_url=None if production else "/openapi.json")

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if settings.api_token:
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                    token.encode("utf-8"), settings.api_token.encode("ascii")):
                response = error(401, "unauthorized", "A valid bearer token is required")
                response.headers["WWW-Authenticate"] = "Bearer"
                response.headers['Cache-Control']='no-store'
                return response
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        return error(422, "invalid_request", "Invalid query parameters")

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc):
        return error(503, "storage_unavailable", "Local storage is unavailable")

    @app.get("/health")
    def health():
        database.healthy()
        return {"status": "ok", "component": "http_api", "database": "ok",
                "mqtt_status": "not_monitored_by_api", "hardware_validated": False}

    @app.get("/api/activities/latest", response_model=Activity)
    def latest(device_id: DeviceFilter = None):
        result = database.latest(device_id)
        return result if result else error(404, "not_found", "No predictions match this device filter")

    @app.get('/api/activities/current')
    def current(device_id: Annotated[str,Query(pattern=r'^[A-Za-z0-9_-]{1,64}$',max_length=64)]):
        return database.current(device_id)

    @app.get("/api/activities/history", response_model=History)
    def history(device_id: DeviceFilter = None,
                limit: Annotated[int, Query(ge=1, le=100)] = 50,
                before_id: Annotated[int | None, Query(ge=1)] = None):
        return database.history(device_id, limit, before_id)

    return app


def main():
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=settings.api_port,
                reload=False, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
