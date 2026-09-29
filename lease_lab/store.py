from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterator

from .engine import projection, propose_event, replay_state


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS scenarios (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at_tick INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS branches (
  id TEXT PRIMARY KEY,
  scenario_id TEXT NOT NULL REFERENCES scenarios(id),
  name TEXT NOT NULL,
  created_from_seq INTEGER NOT NULL DEFAULT 0,
  parent_branch_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
  branch_id TEXT NOT NULL REFERENCES branches(id),
  seq INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  request_id TEXT,
  at_tick INTEGER NOT NULL,
  type TEXT NOT NULL,
  client_id TEXT,
  resource_id TEXT,
  success INTEGER NOT NULL,
  reject_reason TEXT,
  payload_json TEXT NOT NULL,
  client_time INTEGER,
  content_hash TEXT NOT NULL,
  PRIMARY KEY (branch_id, seq),
  UNIQUE (branch_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_branch_seq ON events(branch_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_branch_request ON events(branch_id, request_id);
"""


class VersionConflict(Exception):
    def __init__(self, tail_event: dict[str, Any] | None):
        super().__init__("branch version conflict")
        self.tail_event = tail_event


class IdempotencyConflict(Exception):
    def __init__(self, event: dict[str, Any]):
        super().__init__("idempotency key used with different content")
        self.event = event


class InvalidAction(ValueError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect(database_path: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path or ":memory:", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _row_to_branch(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scenario_id": row["scenario_id"],
        "name": row["name"],
        "created_from_seq": row["created_from_seq"],
        "parent_branch_id": row["parent_branch_id"],
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "request_id": row["request_id"],
        "seq": row["seq"],
        "at_tick": row["at_tick"],
        "type": row["type"],
        "client_id": row["client_id"],
        "resource_id": row["resource_id"],
        "success": bool(row["success"]),
        "reject_reason": row["reject_reason"],
        "payload": json.loads(row["payload_json"]),
        "client_time": row["client_time"],
        "content_hash": row["content_hash"],
    }


def _event_to_action(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event["type"],
        "client_id": event["client_id"],
        "resource_id": event["resource_id"],
        "payload": event["payload"],
    }


def list_scenarios(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM scenarios ORDER BY name, id").fetchall()
    return [dict(row) for row in rows]


def create_scenario(
    conn: sqlite3.Connection, name: str, scenario_id: str | None = None
) -> dict[str, Any]:
    scenario = {"id": scenario_id or new_id("scn"), "name": name, "created_at_tick": 0}
    conn.execute(
        "INSERT INTO scenarios (id, name, created_at_tick) VALUES (?, ?, 0)",
        (scenario["id"], scenario["name"]),
    )
    return scenario


def get_scenario(conn: sqlite3.Connection, scenario_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM scenarios WHERE id=?", (scenario_id,)
    ).fetchone()
    return dict(row) if row else None


def list_branches(conn: sqlite3.Connection, scenario_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM branches WHERE scenario_id=? ORDER BY name, id",
        (scenario_id,),
    ).fetchall()
    result = []
    for row in rows:
        branch = _row_to_branch(row)
        branch["tail_seq"] = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE branch_id=?",
            (branch["id"],),
        ).fetchone()[0]
        result.append(branch)
    return result


def create_branch(
    conn: sqlite3.Connection,
    scenario_id: str,
    name: str,
    branch_id: str | None = None,
    parent_branch_id: str | None = None,
    from_seq: int | None = None,
) -> dict[str, Any]:
    branch_id = branch_id or new_id("br")
    parent_tail = 0
    if parent_branch_id is not None:
        row = conn.execute(
            "SELECT * FROM branches WHERE id=? AND scenario_id=?",
            (parent_branch_id, scenario_id),
        ).fetchone()
        if row is None:
            raise KeyError("parent branch not found")
        parent_tail = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE branch_id=?",
            (parent_branch_id,),
        ).fetchone()[0]
    cutoff = parent_tail if from_seq is None else min(from_seq, parent_tail)
    conn.execute(
        """
        INSERT INTO branches
          (id, scenario_id, name, created_from_seq, parent_branch_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (branch_id, scenario_id, name, cutoff, parent_branch_id),
    )
    if parent_branch_id:
        conn.execute(
            """
            INSERT INTO events (
              branch_id, seq, event_id, request_id, at_tick, type, client_id,
              resource_id, success, reject_reason, payload_json, client_time,
              content_hash
            )
            SELECT ?, seq, event_id, request_id, at_tick, type, client_id,
                   resource_id, success, reject_reason, payload_json, client_time,
                   content_hash
            FROM events
            WHERE branch_id=? AND seq<=?
            ORDER BY seq
            """,
            (branch_id, parent_branch_id, cutoff),
        )
    return get_branch(conn, branch_id)


def get_branch(conn: sqlite3.Connection, branch_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM branches WHERE id=?", (branch_id,)).fetchone()
    if row is None:
        raise KeyError("branch not found")
    branch = _row_to_branch(row)
    branch["tail_seq"] = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM events WHERE branch_id=?", (branch_id,)
    ).fetchone()[0]
    return branch


def list_events(conn: sqlite3.Connection, branch_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM events WHERE branch_id=? ORDER BY seq", (branch_id,)
    ).fetchall()
    return [_row_to_event(row) for row in rows]


def _content_hash(action: dict[str, Any]) -> str:
    normalized = json.dumps(
        {
            "type": action.get("type"),
            "client_id": action.get("client_id"),
            "resource_id": action.get("resource_id"),
            "payload": action.get("payload") or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, normalized))


def _find_idempotent(
    conn: sqlite3.Connection, branch_id: str, request_id: str
) -> tuple[str | None, sqlite3.Row | None]:
    row = conn.execute(
        "SELECT * FROM events WHERE branch_id=? AND request_id=? ORDER BY seq DESC LIMIT 1",
        (branch_id, request_id),
    ).fetchone()
    return (row["branch_id"], row) if row else (None, None)


def append_action(
    conn: sqlite3.Connection,
    branch_id: str,
    action: dict[str, Any],
    *,
    expected_version: int,
    request_id: str | None = None,
    event_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    branch = get_branch(conn, branch_id)
    request_id = request_id or new_id("req")
    existing_branch, row = _find_idempotent(conn, branch_id, request_id)
    if row is not None:
        existing = _row_to_event(row)
        if existing["content_hash"] != _content_hash(action):
            raise IdempotencyConflict(existing)
        return existing, True

    if branch["tail_seq"] != expected_version:
        tail_rows = conn.execute(
            "SELECT * FROM events WHERE branch_id=? ORDER BY seq DESC LIMIT 1",
            (branch_id,),
        ).fetchall()
        tail = _row_to_event(tail_rows[0]) if tail_rows else None
        raise VersionConflict(tail)

    events = list_events(conn, branch_id)
    state = replay_state(events)
    seq = len(events) + 1
    try:
        event = propose_event(
            state,
            action,
            seq=seq,
            event_id=event_id or new_id("evt"),
            request_id=request_id,
        )
    except ValueError as exc:
        raise InvalidAction(str(exc)) from exc
    event["content_hash"] = _content_hash(action)
    conn.execute(
        """
        INSERT INTO events (
          branch_id, seq, event_id, request_id, at_tick, type, client_id,
          resource_id, success, reject_reason, payload_json, client_time,
          content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            branch_id,
            event["seq"],
            event["event_id"],
            event["request_id"],
            event["at_tick"],
            event["type"],
            event["client_id"],
            event["resource_id"],
            int(event["success"]),
            event["reject_reason"],
            json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
            event["client_time"],
            event["content_hash"],
        ),
    )
    return event, False


def get_view(conn: sqlite3.Connection, branch_id: str) -> dict[str, Any]:
    get_branch(conn, branch_id)
    events = list_events(conn, branch_id)
    view = projection(events)
    view.pop("events", None)
    view["branch"] = get_branch(conn, branch_id)
    return view


def export_scenario(conn: sqlite3.Connection, scenario_id: str) -> dict[str, Any]:
    scenario = get_scenario(conn, scenario_id)
    if scenario is None:
        raise KeyError("scenario not found")
    branches = []
    for branch in list_branches(conn, scenario_id):
        events = list_events(conn, branch["id"])
        branches.append({"branch": branch, "events": events})
    return {"format": "lease-lab/v1", "scenario": scenario, "branches": branches}


def import_scenario(conn: sqlite3.Connection, bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise ValueError("export bundle must be an object")
    if bundle.get("format") != "lease-lab/v1":
        raise ValueError("unsupported export format")
    scenario = bundle.get("scenario")
    branches = bundle.get("branches")
    if not isinstance(scenario, dict) or not isinstance(branches, list):
        raise ValueError("export bundle must contain scenario and branches")
    if not isinstance(scenario.get("id"), str) or not scenario.get("name"):
        raise ValueError("scenario id and name are required")
    if get_scenario(conn, scenario["id"]) is not None:
        raise ValueError("scenario id already exists")
    conn.execute("PRAGMA defer_foreign_keys=ON")
    conn.execute(
        "INSERT INTO scenarios (id, name, created_at_tick) VALUES (?, ?, ?)",
        (scenario["id"], scenario["name"], scenario.get("created_at_tick", 0)),
    )
    for item in branches:
        if not isinstance(item, dict) or not isinstance(item.get("branch"), dict):
            raise ValueError("each branch item must contain branch and events")
        branch = item["branch"]
        events = item.get("events")
        if not isinstance(events, list):
            raise ValueError("branch events must be a list")
        conn.execute(
            """
            INSERT INTO branches (
              id, scenario_id, name, created_from_seq, parent_branch_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                branch["id"],
                scenario["id"],
                branch["name"],
                branch["created_from_seq"],
                branch["parent_branch_id"],
            ),
        )
        for event in events:
            required = {
                "event_id", "request_id", "seq", "at_tick", "type",
                "success", "payload", "content_hash",
            }
            if not isinstance(event, dict) or not required <= event.keys():
                raise ValueError("invalid event in export bundle")
            conn.execute(
                """
                INSERT INTO events (
                  branch_id, seq, event_id, request_id, at_tick, type, client_id,
                  resource_id, success, reject_reason, payload_json, client_time,
                  content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    branch["id"],
                    event["seq"],
                    event["event_id"],
                    event["request_id"],
                    event["at_tick"],
                    event["type"],
                    event["client_id"],
                    event["resource_id"],
                    int(event["success"]),
                    event["reject_reason"],
                    json.dumps(event["payload"], ensure_ascii=False, sort_keys=True),
                    event["client_time"],
                    event["content_hash"],
                ),
            )
    return get_scenario(conn, scenario["id"])
