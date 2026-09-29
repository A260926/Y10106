"""SQLite 持久化：不可变事件日志 + 分支 + 幂等键 + 乐观版本号。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import Any

from . import core

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id),
    name TEXT NOT NULL,
    UNIQUE (scenario_id, name)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    seq INTEGER NOT NULL,
    idem_key TEXT,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    UNIQUE (branch_id, seq),
    UNIQUE (branch_id, idem_key)
);
"""


class VersionConflict(Exception):
    def __init__(self, tail: dict[str, Any] | None):
        self.tail = tail


class IdempotencyConflict(Exception):
    pass


def _content_hash(action: dict[str, Any]) -> str:
    blob = json.dumps(action, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class Store:
    def __init__(self, path: str = "lease_lab.db"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---------- 场景与分支 ----------

    def create_scenario(self, name: str) -> dict[str, Any]:
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT INTO scenarios(name) VALUES (?)", (name,))
            sid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO branches(scenario_id, name) VALUES (?, 'main')", (sid,)
            )
        return self.get_scenario(sid)

    def get_scenario(self, sid: int) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM scenarios WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(f"scenario {sid} not found")
        branches = self._conn.execute(
            "SELECT * FROM branches WHERE scenario_id=? ORDER BY id", (sid,)
        ).fetchall()
        return {
            "id": row["id"],
            "name": row["name"],
            "branches": [
                {"id": b["id"], "name": b["name"], "version": self._version(b["id"])}
                for b in branches
            ],
        }

    def list_scenarios(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT id FROM scenarios ORDER BY id").fetchall()
        return [self.get_scenario(r["id"]) for r in rows]

    def find_scenario_by_name(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT id FROM scenarios WHERE name=?", (name,)).fetchone()
        return self.get_scenario(row["id"]) if row else None

    def get_branch(self, bid: int) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
        if row is None:
            raise KeyError(f"branch {bid} not found")
        return {
            "id": row["id"],
            "scenario_id": row["scenario_id"],
            "name": row["name"],
            "version": self._version(bid),
        }

    def _version(self, branch_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE branch_id=?", (branch_id,)
        ).fetchone()
        return row["n"]

    # ---------- 事件 ----------

    def _row_to_event(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "branch_id": row["branch_id"],
            "seq": row["seq"],
            "idempotency_key": row["idem_key"],
            "action": json.loads(row["action"]),
            "outcome": json.loads(row["outcome"]),
        }

    def list_events(self, branch_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE branch_id=? ORDER BY seq", (branch_id,)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def tail_event(self, branch_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE branch_id=? ORDER BY seq DESC LIMIT 1",
            (branch_id,),
        ).fetchone()
        return self._row_to_event(row) if row else None

    def branch_state(self, branch_id: int) -> dict[str, Any]:
        events = self.list_events(branch_id)
        return core.replay([e["action"] for e in events])

    def append_action(
        self,
        branch_id: int,
        action: dict[str, Any],
        expected_version: int,
        idem_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """追加动作。返回 (事件, 是否为幂等重放)。

        - expected_version 与当前分支长度不一致 -> VersionConflict(带尾事件)
        - idem_key 已存在且内容相同 -> 返回原事件（replayed=True）
        - idem_key 已存在但内容不同 -> IdempotencyConflict
        """
        with self._lock, self._conn:
            self.get_branch(branch_id)  # 校验存在
            if idem_key is not None:
                row = self._conn.execute(
                    "SELECT * FROM events WHERE branch_id=? AND idem_key=?",
                    (branch_id, idem_key),
                ).fetchone()
                if row is not None:
                    existing = self._row_to_event(row)
                    if _content_hash(existing["action"]) == _content_hash(action):
                        return existing, True
                    raise IdempotencyConflict(idem_key)

            version = self._version(branch_id)
            if expected_version != version:
                raise VersionConflict(self.tail_event(branch_id))

            state = self.branch_state(branch_id)
            _, outcome = core.apply_action(state, action)
            seq = version + 1
            cur = self._conn.execute(
                "INSERT INTO events(branch_id, seq, idem_key, action, outcome)"
                " VALUES (?,?,?,?,?)",
                (
                    branch_id,
                    seq,
                    idem_key,
                    json.dumps(action, ensure_ascii=False, sort_keys=True),
                    json.dumps(outcome, ensure_ascii=False, sort_keys=True),
                ),
            )
            event = {
                "id": cur.lastrowid,
                "branch_id": branch_id,
                "seq": seq,
                "idempotency_key": idem_key,
                "action": action,
                "outcome": outcome,
            }
        return event, False

    # ---------- 分支 fork ----------

    def fork(self, branch_id: int, name: str, at_seq: int | None = None) -> dict[str, Any]:
        """从分支的指定位置 fork；父分支事件不被覆盖。"""
        with self._lock, self._conn:
            branch = self.get_branch(branch_id)
            events = self.list_events(branch_id)
            if at_seq is not None:
                events = [e for e in events if e["seq"] <= at_seq]
            cur = self._conn.execute(
                "INSERT INTO branches(scenario_id, name) VALUES (?,?)",
                (branch["scenario_id"], name),
            )
            new_id = cur.lastrowid
            for e in events:
                self._conn.execute(
                    "INSERT INTO events(branch_id, seq, idem_key, action, outcome)"
                    " VALUES (?,?,?,?,?)",
                    (
                        new_id,
                        e["seq"],
                        e["idempotency_key"],
                        json.dumps(e["action"], ensure_ascii=False, sort_keys=True),
                        json.dumps(e["outcome"], ensure_ascii=False, sort_keys=True),
                    ),
                )
        return self.get_branch(new_id)

    # ---------- 导出 / 导入 ----------

    def export_scenario(self, sid: int) -> dict[str, Any]:
        scenario = self.get_scenario(sid)
        return {
            "format": "lease-lab-export-v1",
            "name": scenario["name"],
            "branches": [
                {"name": b["name"], "events": self.list_events(b["id"])}
                for b in scenario["branches"]
            ],
        }

    def import_scenario(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("format") != "lease-lab-export-v1":
            raise ValueError("unsupported export format")
        name = data["name"]
        suffix = 1
        while self.find_scenario_by_name(name) is not None:
            suffix += 1
            name = f'{data["name"]} (imported {suffix})'
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT INTO scenarios(name) VALUES (?)", (name,))
            sid = cur.lastrowid
            for b in data["branches"]:
                cur = self._conn.execute(
                    "INSERT INTO branches(scenario_id, name) VALUES (?,?)",
                    (sid, b["name"]),
                )
                bid = cur.lastrowid
                for e in sorted(b["events"], key=lambda x: x["seq"]):
                    self._conn.execute(
                        "INSERT INTO events(branch_id, seq, idem_key, action, outcome)"
                        " VALUES (?,?,?,?,?)",
                        (
                            bid,
                            e["seq"],
                            e["idempotency_key"],
                            json.dumps(e["action"], ensure_ascii=False, sort_keys=True),
                            json.dumps(e["outcome"], ensure_ascii=False, sort_keys=True),
                        ),
                    )
        return self.get_scenario(sid)
