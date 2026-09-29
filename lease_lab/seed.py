from __future__ import annotations

import sqlite3

from .store import append_action, create_branch, create_scenario


SCENARIO_ID = "scn_preset_lease_lab"
BRANCH_ID = "br_preset_main"
RESOURCE_ID = "res_demo"
ALPHA_ID = "cli_alpha"
BETA_ID = "cli_beta"


def seed_demo(conn: sqlite3.Connection) -> bool:
    if conn.execute(
        "SELECT 1 FROM scenarios WHERE id=?", (SCENARIO_ID,)
    ).fetchone():
        return False

    create_scenario(conn, "租约语义演示：接管与晚写", SCENARIO_ID)
    create_branch(conn, SCENARIO_ID, "main", BRANCH_ID)

    steps = [
        (
            {"type": "create_resource", "resource_id": RESOURCE_ID,
             "payload": {"name": "共享配置 X"}},
            "req_seed_01",
            "evt_seed_01",
        ),
        (
            {"type": "create_client", "client_id": ALPHA_ID,
             "payload": {"name": "客户端 A（旧持有者）"}},
            "req_seed_02",
            "evt_seed_02",
        ),
        (
            {"type": "create_client", "client_id": BETA_ID,
             "payload": {"name": "客户端 B（接管者）"}},
            "req_seed_03",
            "evt_seed_03",
        ),
        (
            {"type": "acquire", "client_id": ALPHA_ID, "resource_id": RESOURCE_ID,
             "payload": {"ttl": 4}},
            "req_seed_04",
            "evt_seed_04",
        ),
        (
            {"type": "write", "client_id": ALPHA_ID, "resource_id": RESOURCE_ID,
             "payload": {"token": 1, "value": "A@1 accepted"}},
            "req_seed_05",
            "evt_seed_05",
        ),
        (
            {"type": "pause", "client_id": ALPHA_ID, "payload": {}},
            "req_seed_06",
            "evt_seed_06",
        ),
        (
            {"type": "partition", "client_id": ALPHA_ID, "payload": {}},
            "req_seed_07",
            "evt_seed_07",
        ),
        (
            {"type": "tick", "client_id": ALPHA_ID, "payload": {"ticks": 4}},
            "req_seed_08",
            "evt_seed_08",
        ),
        (
            {"type": "acquire", "client_id": BETA_ID, "resource_id": RESOURCE_ID,
             "payload": {"ttl": 4}},
            "req_seed_09",
            "evt_seed_09",
        ),
        (
            {"type": "write", "client_id": BETA_ID, "resource_id": RESOURCE_ID,
             "payload": {"token": 2, "value": "B@2 accepted"}},
            "req_seed_10",
            "evt_seed_10",
        ),
        (
            {"type": "write", "client_id": ALPHA_ID, "resource_id": RESOURCE_ID,
             "payload": {"token": 1, "value": "A@1 late write"}},
            "req_seed_11",
            "evt_seed_11",
        ),
    ]

    version = 0
    for action, request_id, event_id in steps:
        event, _ = append_action(
            conn,
            BRANCH_ID,
            action,
            expected_version=version,
            request_id=request_id,
            event_id=event_id,
        )
        version = event["seq"]
    conn.commit()
    return True
