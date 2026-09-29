from __future__ import annotations

from copy import deepcopy
from typing import Any


LEASE_EXPIRED = "lease_expired"
NOT_HOLDER = "not_holder"
STALE_TOKEN = "stale_token"


def initial_state() -> dict[str, Any]:
    return {
        "time": 0,
        "next_token": 1,
        "resources": {},
        "clients": {},
        "leases": {},
        "last_event_seq": 0,
    }


def lease_active(lease: dict[str, Any] | None, at_time: int | None = None) -> bool:
    if not lease:
        return False
    current = lease["expires_at"] if at_time is None else at_time
    return current < lease["expires_at"]


def _client_exists(state: dict[str, Any], client_id: str) -> bool:
    return client_id in state["clients"]


def _resource_exists(state: dict[str, Any], resource_id: str) -> bool:
    return resource_id in state["resources"]


def _lease_for_client(
    state: dict[str, Any], resource_id: str, client_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    lease = state["leases"].get(resource_id)
    if not lease:
        return None, NOT_HOLDER
    if not lease_active(lease, state["time"]):
        if lease["holder_id"] != client_id:
            return lease, NOT_HOLDER
        return lease, LEASE_EXPIRED
    if lease["holder_id"] != client_id:
        return lease, NOT_HOLDER
    return lease, None


def _validate_common(
    state: dict[str, Any], action: dict[str, Any], require_client: bool = True
) -> str | None:
    resource_id = action.get("resource_id")
    client_id = action.get("client_id")
    if resource_id is not None and not _resource_exists(state, resource_id):
        return "unknown_resource"
    if require_client and (
        not client_id or not _client_exists(state, client_id)
    ):
        return "unknown_client"
    return None


def _positive_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def propose_event(
    state: dict[str, Any],
    action: dict[str, Any],
    *,
    seq: int,
    event_id: str,
    request_id: str,
) -> dict[str, Any]:
    action_type = action.get("type")
    payload = action.get("payload") or {}
    client_id = action.get("client_id")
    resource_id = action.get("resource_id")
    success = True
    reject_reason: str | None = None
    event_payload = deepcopy(payload)

    if action_type == "create_resource":
        if not resource_id or not payload.get("name"):
            raise ValueError("resource id and name are required")
        if resource_id in state["resources"]:
            raise ValueError("resource already exists")
    elif action_type == "create_client":
        if not client_id or not payload.get("name"):
            raise ValueError("client id and name are required")
        if client_id in state["clients"]:
            raise ValueError("client already exists")
    elif action_type == "tick":
        ticks = _positive_int(payload, "ticks")
        if ticks is None:
            raise ValueError("ticks must be a positive integer")
        event_payload["new_time"] = state["time"] + ticks
    else:
        require_client = action_type not in {"create_resource", "create_client"}
        error = _validate_common(
            state, action, require_client=require_client
        )
        if error:
            raise ValueError(error.replace("_", " "))
        client = state["clients"][client_id]
        if action_type == "pause":
            if client["paused"]:
                success, reject_reason = False, "already_paused"
        elif action_type == "resume":
            if not client["paused"]:
                success, reject_reason = False, "not_paused"
        elif action_type == "partition":
            if client["partitioned"]:
                success, reject_reason = False, "already_partitioned"
            else:
                event_payload["cutoff"] = seq - 2
        elif action_type == "recover":
            if not client["partitioned"]:
                success, reject_reason = False, "not_partitioned"
        elif action_type == "acquire":
            ttl = _positive_int(payload, "ttl")
            if ttl is None:
                raise ValueError("ttl must be a positive integer")
            lease = state["leases"].get(resource_id)
            if lease and lease_active(lease, state["time"]):
                if lease["holder_id"] != client_id:
                    success, reject_reason = False, NOT_HOLDER
        elif action_type == "renew":
            ttl = _positive_int(payload, "ttl")
            if ttl is None:
                raise ValueError("ttl must be a positive integer")
            _, reason = _lease_for_client(state, resource_id, client_id)
            if reason:
                success, reject_reason = False, reason
        elif action_type == "write":
            token = payload.get("token")
            value = payload.get("value")
            if isinstance(token, bool) or not isinstance(token, int) or token <= 0:
                raise ValueError("token must be a positive integer")
            if not isinstance(value, str):
                raise ValueError("value must be a string")
            lease = state["leases"].get(resource_id)
            resource = state["resources"][resource_id]
            resource_seen_token = resource["accepted_token"] or 0
            if lease and lease_active(lease, state["time"]):
                if lease["holder_id"] != client_id:
                    reject_reason = STALE_TOKEN if token < lease["token"] else NOT_HOLDER
                elif token != lease["token"] or token < resource_seen_token:
                    reject_reason = STALE_TOKEN
            elif lease:
                if token == lease["token"] and token >= resource_seen_token:
                    reject_reason = (
                        LEASE_EXPIRED
                        if lease["holder_id"] == client_id
                        else NOT_HOLDER
                    )
                else:
                    reject_reason = STALE_TOKEN
            else:
                reject_reason = NOT_HOLDER
            if reject_reason:
                success = False
        else:
            raise ValueError("unknown action type")

    at_tick = event_payload["new_time"] if action_type == "tick" else state["time"]
    return {
        "event_id": event_id,
        "request_id": request_id,
        "seq": seq,
        "at_tick": at_tick,
        "type": action_type,
        "client_id": client_id,
        "resource_id": resource_id,
        "success": success,
        "reject_reason": reject_reason,
        "payload": event_payload,
        "client_time": (
            state["clients"].get(client_id, {}).get("observed_time")
            if client_id
            else None
        ),
    }


def apply_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    next_state = deepcopy(state)
    event_type = event["type"]
    payload = event["payload"]
    next_state["last_event_seq"] = event["seq"]

    if event_type == "tick" and event["success"]:
        ticks = payload["ticks"]
        next_state["time"] += ticks
        for client in next_state["clients"].values():
            if not client["paused"]:
                client["observed_time"] += ticks
        return next_state
    if not event["success"]:
        return next_state

    if event_type == "create_resource":
        next_state["resources"][event["resource_id"]] = {
            "id": event["resource_id"],
            "name": payload["name"],
            "value": None,
            "accepted_token": None,
            "updated_by": None,
            "updated_at": None,
        }
    elif event_type == "create_client":
        next_state["clients"][event["client_id"]] = {
            "id": event["client_id"],
            "name": payload["name"],
            "paused": False,
            "observed_time": next_state["time"],
            "partitioned": False,
            "partition_cutoff": None,
        }
    elif event_type == "pause":
        next_state["clients"][event["client_id"]]["paused"] = True
    elif event_type == "resume":
        client = next_state["clients"][event["client_id"]]
        client["paused"] = False
        client["observed_time"] = next_state["time"]
    elif event_type == "partition":
        client = next_state["clients"][event["client_id"]]
        client["partitioned"] = True
        client["partition_cutoff"] = payload["cutoff"]
    elif event_type == "recover":
        client = next_state["clients"][event["client_id"]]
        client["partitioned"] = False
        client["partition_cutoff"] = None
        client["observed_time"] = next_state["time"]
    elif event_type == "acquire":
        token = next_state["next_token"]
        next_state["next_token"] += 1
        next_state["leases"][event["resource_id"]] = {
            "resource_id": event["resource_id"],
            "holder_id": event["client_id"],
            "token": token,
            "acquired_at": next_state["time"],
            "expires_at": next_state["time"] + payload["ttl"],
        }
    elif event_type == "renew":
        lease = next_state["leases"][event["resource_id"]]
        lease["expires_at"] = next_state["time"] + payload["ttl"]
    elif event_type == "write":
        resource = next_state["resources"][event["resource_id"]]
        resource["value"] = payload["value"]
        resource["accepted_token"] = payload["token"]
        resource["updated_by"] = event["client_id"]
        resource["updated_at"] = next_state["time"]
    return next_state


def replay_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    state = initial_state()
    for event in sorted(events, key=lambda item: item["seq"]):
        state = apply_event(state, event)
    return state


def visible_events(
    state: dict[str, Any], events: list[dict[str, Any]], client_id: str
) -> list[dict[str, Any]]:
    client = state["clients"][client_id]
    ordered = sorted(events, key=lambda item: item["seq"])
    if not client["partitioned"]:
        return ordered
    return [event for event in ordered if event["seq"] <= client["partition_cutoff"]]


def client_view(
    state: dict[str, Any], events: list[dict[str, Any]], client_id: str
) -> dict[str, Any]:
    client = state["clients"][client_id]
    visible = visible_events(state, events, client_id)
    believed_state = replay_state(visible)
    observed_time = client["observed_time"]
    leases = {}
    for resource_id, lease in believed_state["leases"].items():
        visible_lease = deepcopy(lease)
        visible_lease["active"] = observed_time < lease["expires_at"]
        leases[resource_id] = visible_lease
    return {
        "client": deepcopy(client),
        "observed_time": observed_time,
        "visible_event_seq": visible[-1]["seq"] if visible else 0,
        "resources": deepcopy(believed_state["resources"]),
        "leases": leases,
    }


def projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda item: item["seq"])
    state = replay_state(ordered)
    leases = {}
    for resource_id, lease in state["leases"].items():
        leases[resource_id] = {
            **deepcopy(lease),
            "active": lease_active(lease, state["time"]),
        }
    clients = {
        client_id: client_view(state, ordered, client_id)
        for client_id in state["clients"]
    }
    return {
        "time": state["time"],
        "next_token": state["next_token"],
        "resources": deepcopy(state["resources"]),
        "clients": clients,
        "leases": leases,
        "last_event_seq": state["last_event_seq"],
        "events": ordered,
    }
