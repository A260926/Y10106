"""租约语义核心：所有状态都从不可变事件序列推导。

规则固定：
- 租约在 coordinator_time < expires_at 时有效；恰好到期（相等）即失效。
- 每次成功获取产生严格递增的 fencing token（从 1 开始，按资源独立计数）。
- 续约只延长当前持有者的租约，token 不变。
- 资源侧写判定区分三种拒绝：lease_expired / not_holder / stale_token。
- 不使用墙上时间，一切由显式 tick 动作推进。
"""

from __future__ import annotations

import copy
from typing import Any

# 写判定结果常量
ACCEPTED = "accepted"
LEASE_EXPIRED = "lease_expired"
NOT_HOLDER = "not_holder"
STALE_TOKEN = "stale_token"
DROPPED = "dropped"


def initial_state() -> dict[str, Any]:
    return {
        "coordinator_time": 0,
        "clients": {},  # name -> {observable_time, paused, partitioned}
        "resources": {},  # name -> {ttl, holder, token, expires_at, issued_token, value}
    }


def lease_valid(state: dict[str, Any], resource: dict[str, Any]) -> bool:
    """到期瞬间归属规则：now == expires_at 时租约已失效。"""
    return (
        resource.get("holder") is not None
        and state["coordinator_time"] < resource["expires_at"]
    )


def judge_write(state: dict[str, Any], res: dict[str, Any], client: str, token: int) -> str:
    """资源侧判定，明确区分 lease_expired / not_holder / stale_token。"""
    latest = res["issued_token"]
    if latest == 0 or token > latest:
        return NOT_HOLDER
    if token < latest:
        return STALE_TOKEN
    # token == latest（最近一次签发的 token）
    if res["holder"] != client:
        return NOT_HOLDER
    if not lease_valid(state, res):
        return LEASE_EXPIRED
    return ACCEPTED


def apply_action(state: dict[str, Any], action: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """对状态应用一个动作，返回 (新状态, 事件结果)。纯函数，不改入参。"""
    new = copy.deepcopy(state)
    kind = action["type"]
    now = new["coordinator_time"]
    outcome: dict[str, Any] = {"ok": True}

    if kind == "create_client":
        name = action["client"]
        if name not in new["clients"]:
            new["clients"][name] = {
                "observable_time": now,
                "paused": False,
                "partitioned": False,
            }
        outcome["client"] = name

    elif kind == "create_resource":
        name = action["resource"]
        if name not in new["resources"]:
            new["resources"][name] = {
                "ttl": int(action["ttl"]),
                "holder": None,
                "token": 0,
                "expires_at": 0,
                "issued_token": 0,
                "value": None,
            }
        outcome["resource"] = name

    elif kind == "tick":
        n = int(action.get("n", 1))
        new["coordinator_time"] = now + n
        for c in new["clients"].values():
            if not c["paused"]:
                c["observable_time"] += n
        outcome["coordinator_time"] = new["coordinator_time"]

    elif kind == "pause":
        c = new["clients"][action["client"]]
        c["paused"] = True
        outcome.update(
            client=action["client"],
            coordinator_time=new["coordinator_time"],
            client_observable_time=c["observable_time"],
        )

    elif kind == "resume":
        c = new["clients"][action["client"]]
        c["paused"] = False
        # 恢复时分别记录协调端时间与客户端可观察时间
        outcome.update(
            client=action["client"],
            coordinator_time=new["coordinator_time"],
            client_observable_time=c["observable_time"],
        )

    elif kind == "partition":
        new["clients"][action["client"]]["partitioned"] = True
        outcome["client"] = action["client"]

    elif kind == "heal":
        new["clients"][action["client"]]["partitioned"] = False
        outcome["client"] = action["client"]

    elif kind == "acquire":
        client, rname = action["client"], action["resource"]
        res = new["resources"][rname]
        if new["clients"][client]["partitioned"]:
            # 分区用动作可见性建模：消息不到达协调端，状态不变
            outcome.update(ok=False, result=DROPPED, reason="partition")
        elif lease_valid(new, res):
            outcome.update(ok=False, result="busy", holder=res["holder"], token=res["token"])
        else:
            res["issued_token"] += 1
            res["token"] = res["issued_token"]
            res["holder"] = client
            res["expires_at"] = new["coordinator_time"] + res["ttl"]
            outcome.update(
                result="granted",
                token=res["token"],
                expires_at=res["expires_at"],
            )

    elif kind == "renew":
        client, rname = action["client"], action["resource"]
        res = new["resources"][rname]
        if new["clients"][client]["partitioned"]:
            outcome.update(ok=False, result=DROPPED, reason="partition")
        elif res["holder"] != client:
            outcome.update(ok=False, result=NOT_HOLDER, holder=res["holder"])
        elif not lease_valid(new, res):
            outcome.update(ok=False, result=LEASE_EXPIRED, expires_at=res["expires_at"])
        else:
            # 续约只延长当前持有者，token 不变
            res["expires_at"] = new["coordinator_time"] + res["ttl"]
            outcome.update(result="renewed", token=res["token"], expires_at=res["expires_at"])

    elif kind == "write":
        client, rname = action["client"], action["resource"]
        token = int(action["token"])
        res = new["resources"][rname]
        if new["clients"][client]["partitioned"]:
            outcome.update(ok=False, result=DROPPED, reason="partition")
        else:
            verdict = judge_write(new, res, client, token)
            outcome.update(
                ok=verdict == ACCEPTED,
                result=verdict,
                current_token=res["token"],
                holder=res["holder"],
            )
            if verdict == ACCEPTED:
                res["value"] = action.get("value")
                outcome["value"] = res["value"]

    else:
        raise ValueError(f"unknown action type: {kind}")

    return new, outcome


def replay(actions: list[dict[str, Any]]) -> dict[str, Any]:
    """从事件（动作）序列推导当前状态。"""
    state = initial_state()
    for action in actions:
        state, _ = apply_action(state, action)
    return state
