# TripAgent — 多约束行程 Agent 可靠性平台

以「北京多日行程规划」为业务载体，构建 Agent 运行所需的四层基础设施：**记忆系统、评测观测、注册发现、框架运行时**。业务侧用确定性约束求解保证行程可执行（LLM 不参与时间排程，ADR-001），基础设施侧保证 Agent 在多轮对话、版本迭代和组件故障下仍然可靠。

- 设计文档：[project.md](project.md)（含 ADR 决策记录、API 契约、数据模型）
- 总体架构：[architecture.svg](architecture.svg)
- 任务清单：[TASKS.md](TASKS.md)（阶段 0–1）

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
.venv/Scripts/alembic upgrade head               # 建 runs/spans 表
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

## 数据来源与导出日期（§14 授权风险缓解）

| 数据 | 来源 | 导出/校对日期 | 说明 |
| --- | --- | --- | --- |
| `data/pois.json`（23 个北京 POI） | 高德 Web 服务 place/text v3 | 2026-11-17 导出；同日人工校对营业时间/门票/时长/评分（`manual_review` 字段） | 坐标/地址来自高德；门票与时刻为公开常识值人工核对，非官方实时数据 |
| `data/matrix.json`（24×24 驾车耗时） | 高德 v3/distance（type=1），OSRM 为兜底通道 | 2026-11-17 预计算落盘 | 抽查与 v3/direction/driving 交叉验证一致；`fare_cny` 为出租车启发式估算（公式见文件 meta），非真实计价 |

运行时不调用任何外部数据 API（`ROUTING_PROVIDER=static`，B7）。

## 项目结构（§11.1）

```
app/            FastAPI 入口 / 配置 / api（plan·runs·pois）/ db（repository·runs）/ solver（VRPTW）/ verifier（独立校验）
                （runtime·memory·registry·eval 随阶段 2–4 落地）
alembic/        schema 迁移（禁止手工改表，§11.4）
scripts/        export_pois / build_matrix（一次性导出）+ check_plans（批量断言，阶段 3 eval runner 前身）
web/            静态前端（无构建；行程视图已落地，Trace / 评测看板随阶段 3）+ nginx.conf（/api 同源反代）
data/           pois.json / matrix.json / eval_cases.jsonl / tripagent.db（库文件不入库）
tests/          pytest（契约 / 求解 / 校验 / 属性测试等 37 项）
```

## 环境变量

见 [.env.example](.env.example)，与 project.md §11.2 对齐。

## CI

GitHub Actions（[ci.yml](.github/workflows/ci.yml)）：`lint`（ruff）+ `test`（pytest，迁移后运行）；main 分支保护要求 PR 必过这两项检查。

## 状态与路线图

当前：**阶段 1 · 最小可跑行程已完成**（2026-11-17，详见 [TASKS.md](TASKS.md)）——`POST /api/plan` 全链路可用：OR-Tools VRPTW 两阶段求解 + 独立校验器零冲突 + 前端逐日行程卡。后续：阶段 2 记忆层（Ledger/Views/Policy + 多轮对话）→ 3 评测观测（40 条全集 + Trace 看板）→ 4 平台化（Registry 灰度回滚）。
