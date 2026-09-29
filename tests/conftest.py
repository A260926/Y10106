from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lease_lab.app import create_app
from lease_lab import store
from lease_lab.seed import (
    ALPHA_ID,
    BRANCH_ID,
    BETA_ID,
    RESOURCE_ID,
    SCENARIO_ID,
    seed_demo,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "lab.db")


@pytest.fixture()
def conn(db_path: str):
    connection = store.connect(db_path)
    store.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture()
def client(db_path: str):
    with TestClient(create_app(db_path)) as test_client:
        yield test_client


def make_scenario(conn, name="case"):
    scenario = store.create_scenario(conn, name)
    branch = store.create_branch(conn, scenario["id"], "main")
    conn.commit()
    return scenario["id"], branch["id"]


def append(conn, branch_id: int, action: dict, version: int, request_id=None):
    conn.execute("BEGIN IMMEDIATE")
    try:
        event, _ = store.append_action(
            conn,
            branch_id,
            action,
            expected_version=version,
            request_id=request_id,
        )
        conn.commit()
        return event
    except Exception:
        conn.rollback()
        raise


def create_resource_and_clients(conn, branch_id):
    version = 0
    actions = [
        {"type": "create_resource", "resource_id": RESOURCE_ID,
         "payload": {"name": "R"}},
        {"type": "create_client", "client_id": ALPHA_ID,
         "payload": {"name": "A"}},
        {"type": "create_client", "client_id": BETA_ID,
         "payload": {"name": "B"}},
    ]
    for action in actions:
        event = append(conn, branch_id, action, version)
        version = event["seq"]
    return version


__all__ = [
    "ALPHA_ID",
    "BRANCH_ID",
    "BETA_ID",
    "RESOURCE_ID",
    "SCENARIO_ID",
    "client",
    "conn",
    "create_resource_and_clients",
    "db_path",
    "make_scenario",
    "seed_demo",
]
