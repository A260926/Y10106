"""FastAPI 应用：租约语义演算器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .store import IdempotencyConflict, Store, VersionConflict

STATIC_DIR = Path(__file__).parent / "static"


class ScenarioIn(BaseModel):
    name: str


class ActionIn(BaseModel):
    expected_version: int
    idempotency_key: str | None = None
    action: dict[str, Any]


class ForkIn(BaseModel):
    name: str
    at_seq: int | None = None


class ImportIn(BaseModel):
    data: dict[str, Any]


# 预置演示场景：token 1 的晚写被 token 2 拒绝
SEED_ACTIONS: list[dict[str, Any]] = [
    {"type": "create_client", "client": "client-A"},
    {"type": "create_client", "client": "client-B"},
    {"type": "create_resource", "resource": "resource-R", "ttl": 10},
    {"type": "acquire", "client": "client-A", "resource": "resource-R"},
    {"type": "pause", "client": "client-A"},
    {"type": "tick", "n": 10},
    {"type": "acquire", "client": "client-B", "resource": "resource-R"},
    {"type": "resume", "client": "client-A"},
    {
        "type": "write",
        "client": "client-A",
        "resource": "resource-R",
        "token": 1,
        "value": "late-write-from-A",
    },
]


def seed_demo(store: Store) -> dict[str, Any]:
    """创建（或复用）预置场景：A 暂停期间租约到期，B 接管拿到 token 2，
    A 恢复后用旧 token 1 晚提交，被资源侧以 stale_token 拒绝。"""
    existing = store.find_scenario_by_name("演示场景")
    if existing is not None:
        return existing
    scenario = store.create_scenario("演示场景")
    branch_id = scenario["branches"][0]["id"]
    version = 0
    for action in SEED_ACTIONS:
        _, _ = store.append_action(
            branch_id, action, expected_version=version, idem_key=f"seed-{version + 1}"
        )
        version += 1
    return store.get_scenario(scenario["id"])


def create_app(db_path: str = "lease_lab.db") -> FastAPI:
    store = Store(db_path)
    app = FastAPI(title="租约语义演算器")
    app.state.store = store

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        return store.list_scenarios()

    @app.post("/api/scenarios", status_code=201)
    def create_scenario(body: ScenarioIn) -> dict[str, Any]:
        return store.create_scenario(body.name)

    @app.post("/api/seed", status_code=200)
    def seed() -> dict[str, Any]:
        return seed_demo(store)

    @app.get("/api/scenarios/{sid}")
    def get_scenario(sid: int) -> dict[str, Any]:
        try:
            return store.get_scenario(sid)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/branches/{bid}/events")
    def list_events(bid: int) -> list[dict[str, Any]]:
        try:
            store.get_branch(bid)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return store.list_events(bid)

    @app.get("/api/branches/{bid}/state")
    def branch_state(bid: int) -> dict[str, Any]:
        try:
            store.get_branch(bid)
        except KeyError as e:
            raise HTTPException(404, str(e))
        return store.branch_state(bid)

    @app.post("/api/branches/{bid}/actions", status_code=201)
    def append_action(bid: int, body: ActionIn) -> JSONResponse:
        try:
            event, replayed = store.append_action(
                bid, body.action, body.expected_version, body.idempotency_key
            )
        except KeyError as e:
            raise HTTPException(404, str(e))
        except VersionConflict as e:
            return JSONResponse(
                status_code=409,
                content={"error": "version_conflict", "tail": e.tail},
            )
        except IdempotencyConflict as e:
            return JSONResponse(
                status_code=409,
                content={"error": "idempotency_conflict", "idempotency_key": str(e)},
            )
        return JSONResponse(
            status_code=200 if replayed else 201,
            content={"event": event, "replayed": replayed},
        )

    @app.post("/api/branches/{bid}/fork", status_code=201)
    def fork(bid: int, body: ForkIn) -> dict[str, Any]:
        try:
            return store.fork(bid, body.name, body.at_seq)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/scenarios/{sid}/export")
    def export_scenario(sid: int) -> dict[str, Any]:
        try:
            return store.export_scenario(sid)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/import", status_code=201)
    def import_scenario(body: ImportIn) -> dict[str, Any]:
        try:
            return store.import_scenario(body.data)
        except ValueError as e:
            raise HTTPException(400, str(e))

    return app
