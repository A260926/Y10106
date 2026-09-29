# 租约语义演算器

一个基于 FastAPI、SQLite 和虚拟时钟的租约/fencing token 教学实验。系统不读取墙上时间，也没有后台线程；协调端时间只会由 `tick` 动作推进。

## 安装与运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/python -m lease_lab --port 5236
```

打开 <http://127.0.0.1:5236/>，首页标题为“租约语义演算器”。预置场景 `br_preset_main` 会展示：客户端 A 持有 token 1 后被暂停并分区，协调端推进到恰好到期，客户端 B 获取 token 2 并写入成功；A 之后提交 token 1 的晚写，资源侧返回 `stale_token` 且保留 token 2 的值。

## 动作

- `create_resource` / `create_client`：创建实验对象。
- `tick`：把协调端虚拟时间向前推进；暂停客户端的观测时间不推进。
- `acquire`：成功时生成严格递增 token；已有有效租约只能由当前持有者重新获取。
- `renew`：只延长当前持有者的租期，token 不变。
- `write`：携带 fencing token；资源侧拒绝旧 token。
- `pause` / `resume`：分别记录客户端观测时间与协调端时间。
- `partition` / `recover`：用事件可见 cutoff 建模分区，不改动全局时钟。

拒绝原因固定为：

- `lease_expired`：当前时间已经等于或超过过期点。
- `not_holder`：请求者不是当前租约持有者。
- `stale_token`：写入携带的 token 旧于当前有效租约或资源已接受的 token。

## 一致性

事件不可变，当前租约、资源值、token 序号、暂停状态和分区视图均由事件重放得到。追加动作使用分支尾 `expected_version` 做乐观并发控制；相同 `request_id` 且内容一致时返回原事件，内容不同返回冲突。分支 fork 会复制指定前缀，父分支继续独立追加。导出 JSON 后重新导入会保留事件 ID、事件序、token 和分支关系。

## 主要 API

- `GET /api/scenarios`
- `POST /api/scenarios`
- `POST /api/scenarios/{id}/branches`
- `GET /api/branches/{id}`
- `POST /api/branches/{id}/actions`
- `GET /api/scenarios/{id}/export`
- `POST /api/import`
