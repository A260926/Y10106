from __future__ import annotations

from fastapi.testclient import TestClient

from lease_lab import store
from lease_lab.seed import (
    ALPHA_ID,
    BRANCH_ID,
    BETA_ID,
    RESOURCE_ID,
    SCENARIO_ID,
)

from conftest import append, create_resource_and_clients, make_scenario


def acquire(ttl=4):
    return {
        "type": "acquire",
        "client_id": ALPHA_ID,
        "resource_id": RESOURCE_ID,
        "payload": {"ttl": ttl},
    }


def renew(client_id=ALPHA_ID, ttl=3):
    return {
        "type": "renew",
        "client_id": client_id,
        "resource_id": RESOURCE_ID,
        "payload": {"ttl": ttl},
    }


def write(client_id=ALPHA_ID, token=1, value="v"):
    return {
        "type": "write",
        "client_id": client_id,
        "resource_id": RESOURCE_ID,
        "payload": {"token": token, "value": value},
    }


def tick(ticks):
    return {"type": "tick", "client_id": ALPHA_ID, "payload": {"ticks": ticks}}


def test_exact_expiry_is_expired(conn):
    _, branch_id = make_scenario(conn)
    version = create_resource_and_clients(conn, branch_id)
    event = append(conn, branch_id, acquire(4), version)
    assert event["success"] and event["payload"].get("token", 1) is not False
    version += 1
    version = append(conn, branch_id, tick(4), version)["seq"]
    expired = append(conn, branch_id, write(ALPHA_ID, 1, "late"), version)
    assert expired["success"] is False
    assert expired["reject_reason"] == "lease_expired"
    same_holder_token = append(
        conn, branch_id, write(ALPHA_ID, 1, "same token"), expired["seq"]
    )
    assert same_holder_token["reject_reason"] == "lease_expired"
    view = store.get_view(conn, branch_id)
    assert view["time"] == 4
    assert view["leases"][RESOURCE_ID]["active"] is False


def test_duplicate_renew_extends_same_token(conn):
    _, branch_id = make_scenario(conn)
    version = create_resource_and_clients(conn, branch_id)
    version = append(conn, branch_id, acquire(3), version)["seq"]
    version = append(conn, branch_id, tick(2), version)["seq"]
    first = append(conn, branch_id, renew(ttl=3), version)
    assert first["success"] is True
    version += 1
    version = append(conn, branch_id, tick(2), version)["seq"]
    second = append(conn, branch_id, renew(ttl=5), version)
    assert second["success"] is True
    view = store.get_view(conn, branch_id)
    lease = view["leases"][RESOURCE_ID]
    assert lease["token"] == 1
    assert lease["expires_at"] == 9


def test_partition_takeover_rejects_old_write(conn):
    _, branch_id = make_scenario(conn)
    version = create_resource_and_clients(conn, branch_id)
    version = append(conn, branch_id, acquire(4), version)["seq"]
    version = append(conn, branch_id, write(value="old accepted"), version)["seq"]
    version = append(
        conn,
        branch_id,
        {"type": "partition", "client_id": ALPHA_ID, "payload": {}},
        version,
    )["seq"]
    version = append(conn, branch_id, tick(4), version)["seq"]
    version = append(
        conn,
        branch_id,
        {**acquire(4), "client_id": BETA_ID},
        version,
    )["seq"]
    takeover = append(conn, branch_id, write(BETA_ID, 2, "new"), version)
    assert takeover["success"] is True
    version += 1
    stale = append(conn, branch_id, write(ALPHA_ID, 1, "late"), version)
    assert stale["success"] is False
    assert stale["reject_reason"] == "stale_token"
    view = store.get_view(conn, branch_id)
    assert view["resources"][RESOURCE_ID]["value"] == "new"
    assert view["resources"][RESOURCE_ID]["accepted_token"] == 2
    assert view["clients"][ALPHA_ID]["observed_time"] == 4
    assert view["clients"][ALPHA_ID]["observed_time"] == 4


def test_pause_resume_tracks_separate_observed_time(conn):
    _, branch_id = make_scenario(conn)
    version = create_resource_and_clients(conn, branch_id)
    version = append(
        conn,
        branch_id,
        {"type": "pause", "client_id": ALPHA_ID, "payload": {}},
        version,
    )["seq"]
    version = append(conn, branch_id, tick(3), version)["seq"]
    paused_view = store.get_view(conn, branch_id)
    assert paused_view["time"] == 3
    assert paused_view["clients"][ALPHA_ID]["observed_time"] == 0
    version = append(
        conn,
        branch_id,
        {"type": "resume", "client_id": ALPHA_ID, "payload": {}},
        version,
    )["seq"]
    resumed = append(conn, branch_id, tick(2), version)
    assert resumed["success"] is True
    view = store.get_view(conn, branch_id)
    assert view["time"] == 5
    assert view["clients"][ALPHA_ID]["observed_time"] == 5


def test_tick_can_be_global_without_client(conn):
    _, branch_id = make_scenario(conn)
    version = create_resource_and_clients(conn, branch_id)
    event = append(
        conn,
        branch_id,
        {"type": "tick", "client_id": None, "payload": {"ticks": 2}},
        version,
    )
    assert event["success"] is True
    view = store.get_view(conn, branch_id)
    assert view["time"] == 2
    assert view["clients"][ALPHA_ID]["observed_time"] == 2


def test_branches_are_isolated_after_fork(conn):
    _, branch_id = make_scenario(conn, "parent")
    version = create_resource_and_clients(conn, branch_id)
    version = append(conn, branch_id, acquire(4), version)["seq"]
    forked = store.create_branch(
        conn,
        store.get_branch(conn, branch_id)["scenario_id"],
        "fork",
        parent_branch_id=branch_id,
    )
    conn.commit()
    append(conn, branch_id, tick(1), version)
    fork_event = append(
        conn,
        forked["id"],
        {"type": "renew", "client_id": BETA_ID, "resource_id": RESOURCE_ID,
         "payload": {"ttl": 4}},
        version,
    )
    assert fork_event["reject_reason"] == "not_holder"
    assert store.get_view(conn, branch_id)["time"] == 1
    assert store.get_view(conn, forked["id"])["time"] == 0


def test_export_reimport_preserves_tokens_events_and_order(tmp_path):
    source = str(tmp_path / "source.db")
    first = store.connect(source)
    store.init_db(first)
    _, branch_id = make_scenario(first)
    version = create_resource_and_clients(first, branch_id)
    version = append(first, branch_id, acquire(4), version)["seq"]
    version = append(first, branch_id, tick(4), version)["seq"]
    version = append(first, branch_id, write(ALPHA_ID, 1, "x"), version)["seq"]
    scenario_id = first.execute(
        "SELECT scenario_id FROM branches WHERE id=?", (branch_id,)
    ).fetchone()[0]
    bundle = store.export_scenario(first, scenario_id)
    first.close()

    target = store.connect(str(tmp_path / "target.db"))
    store.init_db(target)
    store.import_scenario(target, bundle)
    target.commit()
    imported_view = store.get_view(target, branch_id)
    source2 = store.connect(source)
    original_view = store.get_view(source2, branch_id)
    assert imported_view["next_token"] == original_view["next_token"]
    assert imported_view["leases"] == original_view["leases"]
    assert store.list_events(target, branch_id) == store.list_events(source2, branch_id)
    source2.close()
    target.close()


def test_concurrent_append_reports_current_tail(client: TestClient):
    scenarios = client.get("/api/scenarios").json()
    preset = next(item for item in scenarios if item["id"] == SCENARIO_ID)
    branches = client.get(f"/api/scenarios/{preset['id']}").json()["branches"]
    branch_id = branches[0]["id"]
    current = client.get(f"/api/branches/{branch_id}").json()
    version = current["view"]["last_event_seq"]
    action = {
        "expected_version": version,
        "type": "tick",
        "client_id": ALPHA_ID,
        "payload": {"ticks": 1},
    }
    first = client.post(f"/api/branches/{branch_id}/actions", json=action)
    second = client.post(f"/api/branches/{branch_id}/actions", json=action)
    assert first.status_code == 201
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["current_tail"]["seq"] == version + 1


def test_preset_page_shows_stale_token_and_title(client: TestClient):
    page = client.get("/").text
    assert "租约语义演算器" in page
    branch = client.get(f"/api/branches/{BRANCH_ID}").json()
    last = branch["events"][-1]
    assert last["seq"] == 11
    assert last["payload"]["token"] == 1
    assert last["reject_reason"] == "stale_token"
    assert branch["view"]["resources"][RESOURCE_ID]["accepted_token"] == 2
    alpha = branch["view"]["clients"][ALPHA_ID]
    assert alpha["observed_time"] == 0
    assert alpha["visible_event_seq"] == 5
    assert alpha["leases"][RESOURCE_ID]["active"] is True


def test_idempotency_same_content_returns_original_and_different_conflicts(
    client: TestClient,
):
    scenario = client.post("/api/scenarios", json={"name": "idem"}).json()
    branch = client.get(
        f"/api/scenarios/{scenario['id']}"
    ).json()["branches"][0]["id"]
    create_resource = {
        "expected_version": 0,
        "type": "create_resource",
        "resource_id": RESOURCE_ID,
        "payload": {"name": "R"},
    }
    create_client = {
        "expected_version": 1,
        "type": "create_client",
        "client_id": ALPHA_ID,
        "payload": {"name": "A"},
    }
    client.post(f"/api/branches/{branch}/actions", json=create_resource)
    client.post(f"/api/branches/{branch}/actions", json=create_client)
    action = {
        "expected_version": 2,
        "request_id": "same-key",
        "type": "tick",
        "client_id": ALPHA_ID,
        "payload": {"ticks": 1},
    }
    first = client.post(f"/api/branches/{branch}/actions", json=action)
    assert first.status_code == 201
    retry = client.post(f"/api/branches/{branch}/actions", json=action)
    assert retry.status_code == 200
    assert retry.json()["idempotent"] is True
    action["payload"] = {"ticks": 2}
    conflict = client.post(f"/api/branches/{branch}/actions", json=action)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"] == "idempotency_conflict"
