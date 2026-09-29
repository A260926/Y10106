"""租约语义演算器测试：全部通过显式 tick 推进，不使用 sleep。"""

import pytest
from fastapi.testclient import TestClient

from lease_lab.app import create_app


@pytest.fixture()
def client():
    app = create_app(":memory:")
    with TestClient(app) as c:
        yield c


def make_scenario(c, ttl=5):
    sid = c.post("/api/scenarios", json={"name": "t"}).json()["id"]
    bid = c.get(f"/api/scenarios/{sid}").json()["branches"][0]["id"]
    v = 0
    for action in [
        {"type": "create_client", "client": "A"},
        {"type": "create_client", "client": "B"},
        {"type": "create_client", "client": "C"},
        {"type": "create_resource", "resource": "R", "ttl": ttl},
    ]:
        act(c, bid, action, v)
        v += 1
    return sid, bid, v


def act(c, bid, action, version, key=None):
    r = c.post(
        f"/api/branches/{bid}/actions",
        json={"expected_version": version, "action": action, "idempotency_key": key},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["event"]


def state(c, bid):
    return c.get(f"/api/branches/{bid}/state").json()


# ---------- 恰好到期 ----------

def test_exact_expiry_boundary(client):
    _, bid, v = make_scenario(client, ttl=5)
    ev = act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    assert ev["outcome"]["token"] == 1 and ev["outcome"]["expires_at"] == 5
    # t=4：仍有效
    act(client, bid, {"type": "tick", "n": 4}, v + 1)
    ev = act(client, bid, {"type": "write", "client": "A", "resource": "R",
                           "token": 1, "value": "x"}, v + 2)
    assert ev["outcome"]["result"] == "accepted"
    # t=5：恰好到期瞬间即失效
    act(client, bid, {"type": "tick", "n": 1}, v + 3)
    ev = act(client, bid, {"type": "write", "client": "A", "resource": "R",
                           "token": 1, "value": "y"}, v + 4)
    assert ev["outcome"]["result"] == "lease_expired"
    assert state(client, bid)["resources"]["R"]["value"] == "x"  # 晚写未生效


# ---------- 重复续约（幂等 + token 不变） ----------

def test_renew_keeps_token_and_idempotent_replay(client):
    _, bid, v = make_scenario(client, ttl=5)
    act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    act(client, bid, {"type": "tick", "n": 4}, v + 1)
    renew = {"type": "renew", "client": "A", "resource": "R"}
    ev1 = act(client, bid, renew, v + 2, key="renew-1")
    assert ev1["outcome"]["result"] == "renewed"
    assert ev1["outcome"]["token"] == 1  # token 不变
    assert ev1["outcome"]["expires_at"] == 9
    # 相同幂等键 + 相同内容 -> 返回原事件，不新增
    r = client.post(f"/api/branches/{bid}/actions",
                    json={"expected_version": v + 3, "action": renew,
                          "idempotency_key": "renew-1"})
    assert r.status_code == 200 and r.json()["replayed"] is True
    assert r.json()["event"]["id"] == ev1["id"]
    assert len(client.get(f"/api/branches/{bid}/events").json()) == v + 3
    # 相同幂等键 + 不同内容 -> 409 冲突
    r = client.post(f"/api/branches/{bid}/actions",
                    json={"expected_version": v + 3,
                          "action": {"type": "renew", "client": "B", "resource": "R"},
                          "idempotency_key": "renew-1"})
    assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict"


# ---------- 分区接管 ----------

def test_partition_then_takeover(client):
    _, bid, v = make_scenario(client, ttl=5)
    act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    act(client, bid, {"type": "partition", "client": "A"}, v + 1)
    # 分区期间 A 的续约不可达协调端
    ev = act(client, bid, {"type": "renew", "client": "A", "resource": "R"}, v + 2)
    assert ev["outcome"]["result"] == "dropped"
    act(client, bid, {"type": "tick", "n": 5}, v + 3)  # t=5，租约到期
    ev = act(client, bid, {"type": "acquire", "client": "B", "resource": "R"}, v + 4)
    assert ev["outcome"]["result"] == "granted" and ev["outcome"]["token"] == 2
    st = state(client, bid)
    assert st["resources"]["R"]["holder"] == "B"
    assert st["clients"]["A"]["partitioned"] is True


# ---------- 旧持有者晚提交（stale token），即使旧客户端时钟仍认为有效 ----------

def test_stale_write_after_takeover(client):
    _, bid, v = make_scenario(client, ttl=5)
    act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    act(client, bid, {"type": "pause", "client": "A"}, v + 1)  # A 的时钟停在 t=0
    act(client, bid, {"type": "tick", "n": 5}, v + 2)
    act(client, bid, {"type": "acquire", "client": "B", "resource": "R"}, v + 3)
    act(client, bid, {"type": "resume", "client": "A"}, v + 4)
    # A 自己的可观察时间仍是 0 < 5，自认为租约有效
    assert state(client, bid)["clients"]["A"]["observable_time"] == 0
    ev = act(client, bid, {"type": "write", "client": "A", "resource": "R",
                           "token": 1, "value": "stale"}, v + 5)
    assert ev["outcome"]["result"] == "stale_token"
    assert ev["outcome"]["current_token"] == 2
    # 旁观者拿当前 token 但不是持有者 -> not_holder
    ev = act(client, bid, {"type": "write", "client": "C", "resource": "R",
                           "token": 2, "value": "x"}, v + 6)
    assert ev["outcome"]["result"] == "not_holder"
    # 当前持有者写入被接受
    ev = act(client, bid, {"type": "write", "client": "B", "resource": "R",
                           "token": 2, "value": "ok"}, v + 7)
    assert ev["outcome"]["result"] == "accepted"
    assert state(client, bid)["resources"]["R"]["value"] == "ok"


# ---------- 暂停 / 恢复：两种时间分别记录 ----------

def test_pause_resume_records_both_clocks(client):
    _, bid, v = make_scenario(client, ttl=100)
    act(client, bid, {"type": "tick", "n": 3}, v)
    ev = act(client, bid, {"type": "pause", "client": "A"}, v + 1)
    assert ev["outcome"]["coordinator_time"] == 3
    assert ev["outcome"]["client_observable_time"] == 3
    act(client, bid, {"type": "tick", "n": 7}, v + 2)
    ev = act(client, bid, {"type": "resume", "client": "A"}, v + 3)
    assert ev["outcome"]["coordinator_time"] == 10
    assert ev["outcome"]["client_observable_time"] == 3  # 暂停期间客户端时钟冻结
    st = state(client, bid)
    assert st["coordinator_time"] == 10
    assert st["clients"]["A"]["observable_time"] == 3
    assert st["clients"]["B"]["observable_time"] == 10


# ---------- 分支隔离 ----------

def test_fork_branch_isolation(client):
    _, bid, v = make_scenario(client, ttl=5)
    act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    fork = client.post(f"/api/branches/{bid}/fork", json={"name": "exp"}).json()
    fb = fork["id"]
    assert fork["version"] == v + 1
    # 子分支：tick 到过期后 B 接管
    act(client, fb, {"type": "tick", "n": 5}, v + 1)
    act(client, fb, {"type": "acquire", "client": "B", "resource": "R"}, v + 2)
    # 父分支：A 续约，互不影响
    act(client, bid, {"type": "renew", "client": "A", "resource": "R"}, v + 1)
    parent = state(client, bid)["resources"]["R"]
    child = state(client, fb)["resources"]["R"]
    assert parent["holder"] == "A" and parent["token"] == 1
    assert child["holder"] == "B" and child["token"] == 2
    # 父分支事件未被覆盖
    assert len(client.get(f"/api/branches/{bid}/events").json()) == v + 2


# ---------- 并发追加（版本号冲突带尾事件） ----------

def test_concurrent_append_version_conflict(client):
    _, bid, v = make_scenario(client)
    # 两个"页面"基于同一版本同时追加
    r1 = client.post(f"/api/branches/{bid}/actions",
                     json={"expected_version": v, "action": {"type": "tick", "n": 1}})
    r2 = client.post(f"/api/branches/{bid}/actions",
                     json={"expected_version": v, "action": {"type": "tick", "n": 2}})
    assert r1.status_code == 201
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"] == "version_conflict"
    assert body["tail"]["seq"] == v + 1  # 响应带当前分支尾事件
    assert body["tail"]["action"] == {"type": "tick", "n": 1}
    # 失败方拿到尾事件后按新版本重试可成功
    r3 = client.post(f"/api/branches/{bid}/actions",
                     json={"expected_version": body["tail"]["seq"],
                           "action": {"type": "tick", "n": 2}})
    assert r3.status_code == 201
    assert state(client, bid)["coordinator_time"] == 3


# ---------- 导出 / 导入：token、状态、事件序一致 ----------

def test_export_import_roundtrip(client):
    sid, bid, v = make_scenario(client, ttl=5)
    act(client, bid, {"type": "acquire", "client": "A", "resource": "R"}, v)
    act(client, bid, {"type": "tick", "n": 5}, v + 1)
    act(client, bid, {"type": "acquire", "client": "B", "resource": "R"}, v + 2)
    client.post(f"/api/branches/{bid}/fork", json={"name": "exp"})
    act(client, bid, {"type": "write", "client": "A", "resource": "R",
                      "token": 1, "value": "late"}, v + 3)

    orig_states = {b["name"]: state(client, b["id"])
                   for b in client.get(f"/api/scenarios/{sid}").json()["branches"]}
    exported = client.get(f"/api/scenarios/{sid}/export").json()
    imported = client.post("/api/import", json={"data": exported}).json()
    assert imported["id"] != sid

    for orig_b, imp_b in zip(exported["branches"], imported["branches"]):
        assert orig_b["name"] == imp_b["name"]
        orig_events = orig_b["events"]
        imp_events = client.get(f"/api/branches/{imp_b['id']}/events").json()
        # 事件序与内容一致
        assert [e["seq"] for e in imp_events] == [e["seq"] for e in orig_events]
        assert [e["action"] for e in imp_events] == [e["action"] for e in orig_events]
        assert [e["outcome"] for e in imp_events] == [e["outcome"] for e in orig_events]
        # 推导状态（含 token）一致
        assert state(client, imp_b["id"]) == orig_states[imp_b["name"]]
    assert state(client, imported["branches"][0]["id"])["resources"]["R"]["token"] == 2


# ---------- 预置场景：token 1 的晚写被 token 2 拒绝 ----------

def test_seed_scenario_shows_stale_token_rejection(client):
    client.post("/api/seed")
    scenarios = client.get("/api/scenarios").json()
    seed = next(s for s in scenarios if s["name"] == "演示场景")
    bid = seed["branches"][0]["id"]
    events = client.get(f"/api/branches/{bid}/events").json()
    tokens = [e["outcome"].get("token") for e in events
              if e["action"]["type"] == "acquire" and e["outcome"].get("result") == "granted"]
    assert tokens == [1, 2]  # 严格递增
    last = events[-1]
    assert last["action"]["type"] == "write" and last["action"]["token"] == 1
    assert last["outcome"]["result"] == "stale_token"
    assert last["outcome"]["current_token"] == 2
    st = state(client, bid)
    assert st["resources"]["resource-R"]["holder"] == "client-B"
    assert st["clients"]["client-A"]["observable_time"] == 0  # A 的时钟停在暂停时
    assert st["coordinator_time"] == 10


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "租约语义演算器" in r.text
