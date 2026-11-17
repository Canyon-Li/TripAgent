# TASKS — 阶段 0–1 任务清单

> 边界依据 `project.md` §0（B1–B13）。勾选即完成；每阶段验收口径写在节末，验收通过即冻结该阶段（§14 范围控制）。阶段 2–4 清单在阶段 1 冻结后按同样格式拆出。

## 阶段 0 · 资源就绪

**目标**：仓库、密钥、数据集、容器底座全部就位；`docker compose up` 后可访问 `/healthz`。

- [x] **0.1 仓库初始化**：`git init`、创建 GitHub 公开仓、main 分支保护（Actions 通过才可合并）、`.gitignore`（.env / data/*.db / reports/ 是否入库另行决定）✅ 2026-11-17 [Canyon-Li/TripAgent](https://github.com/Canyon-Li/TripAgent)；分支保护：必需检查 `test`/`lint`（0.8 CI 落地后对 PR 生效）+ 禁 force push / 删除；reports/ 暂定入库（.gitignore 内注释可切换）
- [x] **0.2 Python 工程**：Python 3.11 + `pyproject.toml`（fastapi / uvicorn / pydantic / ortools / sqlalchemy / alembic / httpx / pytest / openai），Windows 原生可装（B13）✅ 2026-11-17 CPython 3.11.15 + uv venv 安装验证通过（48 包 `uv pip check` 全绿，ortools 9.15 Routing 导入正常）；dev 附加 pytest / ruff
- [x] **0.3 密钥申请**：DeepSeek API key（B3）、高德 Web 服务 key（B6，仅导出脚本用）✅ 2026-11-17 两个 key 均实测通过（DeepSeek /models 可用模型 deepseek-flash / deepseek-v4-pro；高德 place/text v3 命中正常），已填入本地 `.env`
- [x] **0.4 POI 数据集** `data/pois.json`：高德导出北京 18–30 个 POI（名称/坐标/地址），**人工校对**营业时间、门票、建议时长、评分；每条带 `export_date` 与 `source` ✅ 2026-11-17 `scripts/export_pois.py` 导出 23 个 POI（0 缺失、字段校验通过）；营业时间/门票/时长/评分经作者人工校对（`manual_review` 记录在案，`needs_review` 已清空）
- [x] **0.5 距离矩阵** `data/matrix.json`：一次性预计算 n×n 耗时（高德驾车优先，OSRM 兜底，B7），含生成脚本 `scripts/build_matrix.py`（可复跑、记录参数）✅ 2026-11-17 24×24（酒店锚点 + 23 POI），高德 v3/distance 全列命中、0 次 OSRM 兜底；产出 minutes/km/fare_cny（出租车启发式，公式在 meta）；抽查 2 组 OD 与 v3/direction/driving 交叉验证一致（18.0↔19 / 36.9↔37，仅 ceil 差）；v5 接口对该 key 不可用（10017），已按 v3 落盘
- [x] **0.6 服务骨架**：FastAPI 入口 + `/healthz` `/readyz`（返回 version + git_sha）+ Alembic 初始迁移建 `runs` / `spans` 表 ✅ 2026-11-17 uvicorn 实测 `/healthz` 200（version 0.1.0 + git_sha 0515a86）、`/readyz` 200（database ok）；Alembic 迁移 `12cd442bb18f` 建 runs(11 列)/spans(8 列)，与 §9 DDL 一致；SQLite WAL 持久化验证；pytest 2 passed + ruff clean
- [ ] **0.7 容器化**：`docker-compose.yml`（api / web）+ `.env.example`（对齐 §11.2）+ Windows 本地与 VPS 各验证一次（B4 / B13）
- [ ] **0.8 CI 占位**：GitHub Actions——pytest（空测试集）+ lint，PR 必过（B8）
- [ ] **0.9 README**：快速上手、`architecture.svg` 引用、数据来源与导出日期声明（§14 授权风险缓解）

**验收**：`docker compose up` 后 `curl /healthz` 返回 200 且含 git_sha；`pytest -q` 绿；公开仓库可访问；`pois.json` ≥ 18 个 POI 且无缺字段。

## 阶段 1 · 最小可跑行程

**目标**：`POST /api/plan` 输入结构化约束 → OR-Tools 求解 → 独立校验器零冲突 → 前端渲染行程卡。这是 B2 定下的首个可演示里程碑。

- [ ] **1.1 数据访问层**：POI / 矩阵加载与内存缓存（`app/db/repository`），只读、带 schema 校验
- [ ] **1.2 Solver v1**：OR-Tools Routing VRPTW 建模（§8）——m 天 = m 辆车、时间窗 `CumulVar` 区间、预算维度、`AddDisjunction` drop penalty、酒店起终点、`time_limit`
- [ ] **1.3 独立 Verifier**：从路线逐段重算时间线、核对闭馆、重算门票+交通费、查重复访问，输出 `violations[]`；**不复用求解器代码路径**（ADR-001）
- [ ] **1.4 API 契约**：`POST /api/plan` + `PlanRequest` / `PlanResponse` pydantic schema（§10 示例为准）+ trace_id 贯穿 `runs` / `spans`
- [ ] **1.5 不可行处理**：求解无解 / 校验失败时返回明确 `infeasible_reasons`，不硬凑行程
- [ ] **1.6 前端行程视图**：静态页（无构建，B 端三个视图中的第一个）调 `/api/plan`，渲染逐日行程卡（arrive / leave / wait_min / 门票）
- [ ] **1.7 结构化检查脚本**：对批量 plan 断言时间窗冲突 = 0、预算不超、必去点覆盖——阶段 3 eval runner 的前身
- [ ] **1.8 属性测试**：随机生成 POI × 约束组合，断言"校验器通过 ⇒ 无硬约束违反"（§15）
- [ ] **1.9 评测种子用例**：10 条结构化 `/api/plan` 用例入库（40 条全集阶段 3 补齐，含 20% 隐藏）

**验收**：10 条种子用例时间窗冲突 = 0、预算违规 = 0、必去点覆盖断言全过；前端可视化一份北京 3 日行程；`GET /api/runs/{trace_id}` 可查到完整 spans；属性测试 ≥ 100 组随机样本通过。

---

## 后续阶段（占位，阶段 1 冻结后细化）

- **阶段 2 · 记忆层**：Ledger / Views / Policy、多轮约束演进对话（B12）、跨会话演示（B9 用户切换）、Resolver 白名单 function calling 与护栏（B14 / ADR-008）
- **阶段 3 · 评测观测**：40 条全集、双基线（ADR-006）、Trace 瀑布图、看板、CI 回归阻断
- **阶段 4 · 平台化**：Registry、健康检查、solver v1/v2 灰度回滚（ADR-007）、故障注入测试、只读 Agent Card（ADR-009）
