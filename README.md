# TripAgent — 多约束行程 Agent 可靠性平台

以「北京多日行程规划」为业务载体，构建 Agent 运行所需的四层基础设施：**记忆系统、评测观测、注册发现、框架运行时**。业务侧用确定性约束求解保证行程可执行（LLM 不参与时间排程，ADR-001），基础设施侧保证 Agent 在多轮对话、版本迭代和组件故障下仍然可靠。

- 设计文档：[project.md](project.md)（含 ADR 决策记录、API 契约、数据模型）
- 任务清单：[TASKS.md](TASKS.md)（阶段 0–5）

## 快速上手

```bash
cp .env.example .env          # 填入 LLM_API_KEY / AMAP_KEY（后者仅数据导出脚本用）
GIT_SHA=$(git rev-parse --short HEAD) docker compose up --build
# web -> http://localhost:3000
# api -> http://localhost:8000/docs   探针 -> /healthz /readyz
```

本地开发（Windows 原生，B13）：

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv -e ".[dev]"        # 或 pip install -e ".[dev]"
.venv/Scripts/alembic upgrade head               # 建 runs/spans + memory_events/memory_views + eval_results/capabilities 表
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

一次性数据导出（复跑安全，需 AMAP_KEY）：

```bash
.venv/Scripts/python.exe scripts/export_pois.py    # 高德导出 POI -> data/pois.json
.venv/Scripts/python.exe scripts/build_matrix.py   # n×n 驾车耗时矩阵 -> data/matrix.json
```

行程规划与批量断言（阶段 1）：

```bash
# 结构化约束直接求解（跳过意图解析，便于评测）：
curl -X POST http://localhost:8000/api/plan -H "Content-Type: application/json" -d '{
  "city": "beijing", "days": 3, "hotel": "wangfujing", "budget": 10000,
  "daily_minutes_cap": 480, "must_visit": ["forbidden_city"],
  "preferences": ["history", "food"], "session_id": "s-001", "start_date": "2026-11-18"}'
# 种子用例批量断言（时间窗冲突=0 / 预算不超 / 必去覆盖）：
.venv/Scripts/python.exe scripts/check_plans.py --include-hidden
```

多轮对话与记忆回放（阶段 2）：

```bash
# SSE 流式对话（需 LLM_API_KEY；无 key 时自动降级规则模板解析）：
curl -N -X POST http://localhost:8000/api/chat -H "Content-Type: application/json"   -d '{"session_id":"s-1","user_id":"u-alice","message":"预算8000玩2天，必去故宫"}'
# 下一轮只改天数，预算来自记忆注入（applied_constraints.source = memory_views#N）：
curl -N -X POST http://localhost:8000/api/chat -H "Content-Type: application/json"   -d '{"session_id":"s-1","user_id":"u-alice","message":"改成玩3天"}'
# 记忆回放与重建（§13「可回放 Ledger」验收口径）：
curl "http://localhost:8000/api/memory/events?session_id=s-1"
curl -X POST http://localhost:8000/api/memory/rebuild    # -> consistent: true
```

评测与观测（阶段 3，§7.3）：

```bash
# 三列评测（current / A 纯LLM直出 / B 关记忆注入，ADR-006；--real 需 LLM_API_KEY）：
.venv/Scripts/python.exe -m app.eval.runner --baseline all --include-hidden --real
# CI 回归模式（公开集 FakeLLM 确定性，对比 reports/baseline.json，退化即 exit 1）：
.venv/Scripts/python.exe -m app.eval.runner --ci
# 指标有意提升后显式刷新基线（单独 commit）：
.venv/Scripts/python.exe -m app.eval.runner --fake --update-baseline
# 在线触发 + 趋势（前端「评测看板」视图同源）：
curl -X POST http://localhost:8000/api/eval/run -H "Content-Type: application/json" -d '{}'
curl "http://localhost:8000/api/eval/reports"
```

平台化：注册发现与灰度回滚（阶段 4，§7.4 / ADR-007）：

```bash
# 能力卡与心跳状态（solver v1/v2、matrix、verifier、llm_adapter，30s 心跳摘除/回切）：
curl "http://localhost:8000/api/registry/capabilities"
curl "http://localhost:8000/api/registry/health"
# 灰度：solver v2 权重 0→100（SQLite 存绑定，路由时读取）——下一次 /api/plan 即生效：
curl -X POST http://localhost:8000/api/registry/capabilities/solver/v2/weight \
  -H "Content-Type: application/json" -d '{"weight": 100}'
# 回滚 = 改回一行（不重启进程）：
curl -X POST http://localhost:8000/api/registry/capabilities/solver/v2/weight \
  -H "Content-Type: application/json" -d '{"weight": 0}'
# 验证路由版本：跑一次规划看 spans（solver.solve 的 meta.solver_version / registry.route 段）：
curl -X POST http://localhost:8000/api/plan -H "Content-Type: application/json" \
  -d '{"city":"beijing","days":3,"hotel":"wangfujing","budget":10000,"daily_minutes_cap":480,"must_visit":["forbidden_city"],"preferences":["history"],"session_id":"reg-demo"}'
# 只读 Agent Card（A2A 风格自描述，discovery-only，ADR-009）：
curl http://localhost:3000/.well-known/agent.json
```

前端「能力注册表」视图提供同能力：卡片表格 + 心跳徽标 + 权重编辑 + 灰度预设一键切换。

## 数据来源与导出日期（§14 授权风险缓解）

| 数据 | 来源 | 导出/校对日期 | 说明 |
| --- | --- | --- | --- |
| `data/pois.json`（23 个北京 POI） | 高德 Web 服务 place/text v3 | 2026-11-17 导出；同日人工校对营业时间/门票/时长/评分（`manual_review` 字段） | 坐标/地址来自高德；门票与时刻为公开常识值人工核对，非官方实时数据 |
| `data/matrix.json`（24×24 驾车耗时） | 高德 v3/distance（type=1），OSRM 为兜底通道 | 2026-11-17 预计算落盘 | 抽查与 v3/direction/driving 交叉验证一致；`fare_cny` 为出租车启发式估算（公式见文件 meta），非真实计价 |

运行时不调用任何外部数据 API（`ROUTING_PROVIDER=static`，B7）。

## 项目结构（§11.1）

```
app/            FastAPI 入口 / 配置 / api（plan·chat·memory·eval·registry·agent_card·runs·pois）/ db（repository·runs·evals·models）
                memory（Ledger·Policy·Views·Memory.read）/ llm（适配器·白名单工具）/ runtime（Resolver·Responder·pipeline）
                solver（VRPTW，v1/v2 参数化）/ verifier（独立校验）/ eval（runner·graders·report·baseline_a·cases·badcase）
                registry（cards·service·seed·health——能力卡 / 加权路由 / 心跳，§7.4）
alembic/        schema 迁移（禁止手工改表，§11.4）
scripts/        export_pois / build_matrix（一次性导出）+ check_plans（plan 用例批量断言）+ badcase_to_eval（坏例转用例）
web/            静态前端（无构建；行程对话 + 手动规划 + Trace 回放瀑布图 + 评测看板 + 能力注册表五视图）+ nginx.conf（/api 与 /.well-known 同源反代）
data/           pois.json / matrix.json / eval_cases.jsonl（40 条全集，含 20% hidden）/ tripagent.db（库文件不入库）
reports/        eval-<sha>.md 评测报告 + baseline.json（CI 回归基线）
tests/          pytest（契约 / 求解 / 校验 / 属性 / 记忆一致性 / 护栏 / 评测 / 注册路由 / 心跳故障注入 / 文档同步等 184 项）
```

## 环境变量

见 [.env.example](.env.example)，与 project.md §11.2 对齐。其中 `MEMORY_INJECTION=off` 仅评测基线 B 使用（ADR-006，运行时切换见 `app/eval/runner.py` 的 `_with_memory_injection`）。

## CI

GitHub Actions（[ci.yml](.github/workflows/ci.yml)）：`lint`（ruff）+ `typecheck`（mypy，宽松起步）+ `test`（pytest，迁移后运行；含 README 测试计数同步断言）+ `eval`（公开集 FakeLLM 确定性回归，对比 `reports/baseline.json`——硬约束类指标任一退化或 final_pass_rate 降 >2pp 即失败，§12）；main 分支保护要求 PR 必过这四项检查（复核：`gh api repos/Canyon-Li/TripAgent/branches/main/protection --jq .required_status_checks.contexts` → `["test","lint","typecheck","eval"]`）。

## 状态与路线图

当前：**阶段 4 · 平台化已完成**（2026-11-18，详见 [TASKS.md](TASKS.md)）——Capability Registry（§7.4：能力卡 SQLite 存权重与版本绑定，路由时读取，**回滚不重启**）+ solver v1/v2 参数化灰度（ADR-007，确定性加权路由）+ 30s 心跳健康检查（失败自动摘除 / 恢复回切，/readyz 联动）+ §15 故障注入测试（LLM 超时降级 / 能力摘除 503 不硬凑 / stale run interrupted 清扫）+ 只读 discovery-only Agent Card（ADR-009，`/.well-known/agent.json`）+ 前端「能力注册表」视图。

至此阶段 0–4 全部完成（§13）。当前进行：**阶段 5 · 上线与真实使用**（2026-11-18 起，清单见 [TASKS.md](TASKS.md)）——0.7 的 VPS 部署验证并入 5.1，此后以真实使用驱动迭代，不预建功能。
