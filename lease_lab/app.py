from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import store
from .seed import seed_demo
from .web import INDEX_HTML


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1)


class BranchIn(BaseModel):
    name: str = Field(min_length=1)
    parent_branch_id: str | None = None
    from_seq: int | None = Field(default=None, ge=0)


class ActionIn(BaseModel):
    expected_version: int = Field(ge=0)
    request_id: str | None = None
    type: str
    client_id: str | None = None
    resource_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def create_app(database_path: str = "lease_lab.db") -> FastAPI:
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        conn = (
            sqlite3.connect(":memory:", check_same_thread=False)
            if database_path == ":memory:"
            else store.connect(database_path)
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        if database_path == ":memory:":
            app.state.memory_conn = conn
        store.init_db(conn)
        seed_demo(conn)
        if database_path != ":memory:":
            conn.close()
        yield
        if app.state.memory_conn is not None:
            app.state.memory_conn.close()
            app.state.memory_conn = None

    app = FastAPI(
        title="租约语义演算器",
        version="0.1.0",
        telemetry={"auto_configure": False},
        lifespan=lifespan,
    )
    app.state.database_path = database_path
    app.state.memory_conn = None

    def db() -> Iterator[Any]:
        conn = app.state.memory_conn or store.connect(app.state.database_path)
        try:
            yield conn
        finally:
            if app.state.memory_conn is None:
                conn.close()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "title": "租约语义演算器"}

    @app.get("/api/scenarios")
    def scenarios(conn: Any = Depends(db)) -> list[dict[str, Any]]:
        return store.list_scenarios(conn)

    @app.post("/api/scenarios", status_code=status.HTTP_201_CREATED)
    def create_scenario(
        body: ScenarioIn, conn: Any = Depends(db)
    ) -> dict[str, Any]:
        scenario = store.create_scenario(conn, body.name)
        store.create_branch(conn, scenario["id"], "main")
        conn.commit()
        return store.get_scenario(conn, scenario["id"])

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str, conn: Any = Depends(db)) -> dict[str, Any]:
        scenario = store.get_scenario(conn, scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        return {
            "scenario": scenario,
            "branches": store.list_branches(conn, scenario_id),
        }

    @app.post(
        "/api/scenarios/{scenario_id}/branches",
        status_code=status.HTTP_201_CREATED,
    )
    def create_branch(
        scenario_id: str, body: BranchIn, conn: Any = Depends(db)
    ) -> dict[str, Any]:
        if store.get_scenario(conn, scenario_id) is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        try:
            branch = store.create_branch(
                conn,
                scenario_id,
                body.name,
                parent_branch_id=body.parent_branch_id,
                from_seq=body.from_seq,
            )
            conn.commit()
        except KeyError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return branch

    @app.get("/api/branches/{branch_id}")
    def get_branch(branch_id: str, conn: Any = Depends(db)) -> dict[str, Any]:
        try:
            return {
                "branch": store.get_branch(conn, branch_id),
                "view": store.get_view(conn, branch_id),
                "events": store.list_events(conn, branch_id),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="branch not found") from exc

    @app.post("/api/branches/{branch_id}/actions", status_code=status.HTTP_201_CREATED)
    def append_action(
        branch_id: str,
        body: ActionIn,
        response: Response,
        conn: Any = Depends(db),
    ) -> dict[str, Any]:
        action = body.model_dump(
            include={"type", "client_id", "resource_id", "payload"}
        )
        try:
            store.get_branch(conn, branch_id)
            conn.execute("BEGIN IMMEDIATE")
            event, idempotent = store.append_action(
                conn,
                branch_id,
                action,
                expected_version=body.expected_version,
                request_id=body.request_id,
            )
            conn.commit()
            if idempotent:
                response.status_code = status.HTTP_200_OK
        except store.VersionConflict as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_tail": exc.tail_event,
                },
            ) from exc
        except store.IdempotencyConflict as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_conflict",
                    "original_event": exc.event,
                },
            ) from exc
        except store.InvalidAction as exc:
            conn.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail="branch not found") from exc
        return {
            "event": event,
            "idempotent": idempotent,
            "version": event["seq"],
        }

    @app.get("/api/scenarios/{scenario_id}/export")
    def export_scenario(scenario_id: str, conn: Any = Depends(db)) -> dict[str, Any]:
        try:
            return store.export_scenario(conn, scenario_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="scenario not found") from exc

    @app.post("/api/import", status_code=status.HTTP_201_CREATED)
    async def import_scenario(request: Request, conn: Any = Depends(db)) -> dict[str, Any]:
        try:
            bundle = await request.json()
            scenario = store.import_scenario(conn, bundle)
            conn.commit()
            return scenario
        except KeyError as exc:
            conn.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            conn.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app
