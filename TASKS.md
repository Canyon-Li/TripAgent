# TASKS — 阶段 0–5 任务清单

> 边界依据 `project.md` §0（B1–B17）。勾选即完成；每阶段验收口径写在节末，验收通过即冻结该阶段（§14 范围控制）。阶段 4 清单在阶段 3 冻结后按同样格式拆出。

## 阶段 0 · 资源就绪

**目标**：仓库、密钥、数据集、容器底座全部就位；`docker compose up` 后可访问 `/healthz`。

- [x] **0.1 仓库初始化**：`git init`、创建 GitHub 公开仓、main 分支保护（Actions 通过才可合并）、`.gitignore`（.env / data/*.db / reports/ 是否入库另行决定）✅ 2026-11-17 [Canyon-Li/TripAgent](https://github.com/Canyon-Li/TripAgent)；分支保护：必需检查 `test`/`lint`（0.8 CI 落地后对 PR 生效）+ 禁 force push / 删除；reports/ 暂定入库（.gitignore 内注释可切换）
- [x] **0.2 Python 工程**：Python 3.11 + `pyproject.toml`（fastapi / uvicorn / pydantic / ortools / sqlalchemy / alembic / httpx / pytest / openai），Windows 原生可装（B13）✅ 2026-11-17 CPython 3.11.15 + uv venv 安装验证通过（48 包 `uv pip check` 全绿，ortools 9.15 Routing 导入正常）；dev 附加 pytest / ruff
- [x] **0.3 密钥申请**：DeepSeek API key（B3）、高德 Web 服务 key（B6，仅导出脚本用）✅ 2026-11-17 两个 key 均实测通过（DeepSeek /models 可用模型 deepseek-flash / deepseek-v4-pro；高德 place/text v3 命中正常），已填入本地 `.env`
- [x] **0.4 POI 数据集** `data/pois.json`：高德导出北京 18–30 个 POI（名称/坐标/地址），**人工校对**营业时间、门票、建议时长、评分；每条带 `export_date` 与 `source` ✅ 2026-11-17 `scripts/export_pois.py` 导出 23 个 POI（0 缺失、字段校验通过）；营业时间/门票/时长/评分经作者人工校对（`manual_review` 记录在案，`needs_review` 已清空）
- [x] **0.5 距离矩阵** `data/matrix.json`：一次性预计算 n×n 耗时（高德驾车优先，OSRM 兜底，B7），含生成脚本 `scripts/build_matrix.py`（可复跑、记录参数）✅ 2026-11-17 24×24（酒店锚点 + 23 POI），高德 v3/distance 全列命中、0 次 OSRM 兜底；产出 minutes/km/fare_cny（出租车启发式，公式在 meta）；抽查 2 组 OD 与 v3/direction/driving 交叉验证一致（18.0↔19 / 36.9↔37，仅 ceil 差）；v5 接口对该 key 不可用（10017），已按 v3 落盘
- [x] **0.6 服务骨架**：FastAPI 入口 + `/healthz` `/readyz`（返回 version + git_sha）+ Alembic 初始迁移建 `runs` / `spans` 表 ✅ 2026-11-17 uvicorn 实测 `/healthz` 200（version 0.1.0 + git_sha 0515a86）、`/readyz` 200（database ok）；Alembic 迁移 `12cd442bb18f` 建 runs(11 列)/spans(8 列)，与 §9 DDL 一致；SQLite WAL 持久化验证；pytest 2 passed + ruff clean
- [ ] **0.7 容器化**：`docker-compose.yml`（api / web）+ `.env.example`（对齐 §11.2）+ Windows 本地与 VPS 各验证一次（B4 / B13）　🟡 2026-11-17 Windows 本地验证通过：`compose up` 后 `/healthz` 200（git_sha 407807e 构建注入）、`/readyz` database ok（启动即迁移）、web 3000 200；PIP_INDEX_URL 默认阿里云镜像（tuna 对容器 403，pypi.org 直连慢）。**VPS 验证待部署时执行**
- [x] **0.8 CI 占位**：GitHub Actions——pytest（空测试集）+ lint，PR 必过（B8）✅ 2026-11-17 run 44061287410 全绿（`test`/`lint` job 名与分支保护必需检查一致）；test job 含 alembic 迁移步骤，探针契约测试在 CI 环境通过
- [x] **0.9 README**：快速上手、`architecture.svg` 引用、数据来源与导出日期声明（§14 授权风险缓解）✅ 2026-11-17 快速上手（compose/本地开发/数据导出）、数据来源表（POI 高德+人工校对日期、矩阵预计算+交叉验证、fare 启发式声明）、结构说明、CI 说明

**验收**：`docker compose up` 后 `curl /healthz` 返回 200 且含 git_sha；`pytest -q` 绿；公开仓库可访问；`pois.json` ≥ 18 个 POI 且无缺字段。

## 阶段 1 · 最小可跑行程

**目标**：`POST /api/plan` 输入结构化约束 → OR-Tools 求解 → 独立校验器零冲突 → 前端渲染行程卡。这是 B2 定下的首个可演示里程碑。

- [x] **1.1 数据访问层**：POI / 矩阵加载与内存缓存（`app/db/repository`），只读、带 schema 校验 ✅ 2026-11-17 pydantic 严格校验（时间窗含停留可行性、closed_weekdays 0–6、矩阵方形/对角零/非对角正）+ POI↔矩阵节点序一致性校验 + `lru_cache` 单例；tests 8 项
- [x] **1.2 Solver v1**：OR-Tools Routing VRPTW 建模（§8） ✅ 2026-11-17 时间维度按**绝对时刻**建模（CumulVar SetRange(open, close−duration)，T_day 用逐车 span 约束而非 capacity——绝对时刻下 capacity 会错切）；预算维度角制取整（floor 只收紧）+ 跨天总量 ≤ B；`AddDisjunction` penalty=score×W（偏好类目 ×1.5）；周闭馆用 `VehicleVar.RemoveValue`；**两阶段求解**（完整目标 → 必去救援（可选点 penalty=0）→ ReadAssignmentFromRoutes 回灌 warm-start），修复 PATH_CHEAPEST_ARC 在 cap 挤压下丢必去点的搜索缺陷；tests 8 项
- [x] **1.3 独立 Verifier**：从路线逐段重算时间线、核对闭馆、重算门票+交通费、查重复访问，输出 `violations[]`；**不复用求解器代码路径**（ADR-001） ✅ 2026-11-17 `app/verifier/verify.py` 不 import 任何 solver 内容，输入为纯 dataclass 声明；9 类 violation（timeline_mismatch / window_closed / closed_weekday / duplicate_visit / daily_cap_exceeded / budget_exceeded / must_visit_missed / unknown_poi / invalid_day_index）；故意构造违反的 tests 9 项逐类命中 + 真实求解结果端到端零冲突
- [x] **1.4 API 契约**：`POST /api/plan` + `PlanRequest` / `PlanResponse` pydantic schema（§10 示例为准）+ trace_id 贯穿 `runs` / `spans` ✅ 2026-11-17 §10 请求/响应示例逐字段对齐（applied_constraints.source 阶段 1 如实为 "request"）；solver.solve / verifier.check 两个 span 落库；`GET /api/runs/{trace_id}` 可查完整 spans；另加只读 `GET /api/pois` 供前端目录；契约 tests 9 项
- [x] **1.5 不可行处理**：求解无解 / 校验失败时返回明确 `infeasible_reasons`，不硬凑行程 ✅ 2026-11-17 三类原因：`solver_no_solution` / `must_visit_not_covered` / `verification_failed[type]`；infeasible 时 days 恒为空（测试断言）
- [x] **1.6 前端行程视图**：静态页（无构建，B 端三个视图中的第一个）调 `/api/plan`，渲染逐日行程卡（arrive / leave / wait_min / 门票） ✅ 2026-11-17 约束表单（偏好/必去点 chips 从 `/api/pois` 动态加载）→ 逐日行程卡 + 校验横幅 + trace_id/spans 链接；web 容器改为 nginx 同源反代 `/api`（ADR-005，免 CORS）；浏览器实测可行（北京 3 日卡）与不可行（原因列表）两分支
- [x] **1.7 结构化检查脚本**：对批量 plan 断言时间窗冲突 = 0、预算不超、必去点覆盖——阶段 3 eval runner 的前身 ✅ 2026-11-17 `scripts/check_plans.py`（进程内 TestClient + 临时库；--include-hidden / --json）；10/10 全过
- [x] **1.8 属性测试**：随机生成 POI × 约束组合，断言"校验器通过 ⇒ 无硬约束违反"（§15） ✅ 2026-11-17 固定种子 120 组随机合成世界（非对称矩阵/随机闭馆/紧预算）：①求解成功且必去覆盖 ⇒ 校验器零冲突 ②校验器通过 ⇒ 测试内第三方 oracle 重算零违反 ③不可行 ⇒ 必有明确证据；实测 74 组走可行+双实现咬合路径、46 组走不可行证据路径
- [x] **1.9 评测种子用例**：10 条结构化 `/api/plan` 用例入库（40 条全集阶段 3 补齐，含 20% 隐藏） ✅ 2026-11-17 `data/eval_cases.jsonl`：8 公开 + 2 hidden（4 日远郊多必去 / 预算 1 元），覆盖可行 6 / 不可行 4

**验收**：10 条种子用例时间窗冲突 = 0、预算违规 = 0、必去点覆盖断言全过；前端可视化一份北京 3 日行程；`GET /api/runs/{trace_id}` 可查到完整 spans；属性测试 ≥ 100 组随机样本通过。　✅ 2026-11-17 全过：种子 10/10（含 hidden）；浏览器实测 3 日行程卡渲染（含周末闭馆规避）；runs/spans 含 solver+verifier 完整可查（compose 经 nginx 反代实测）；属性测试 120 组；pytest 37 passed + ruff clean；`docker compose up` 冒烟通过（/healthz、web 3000、/api/plan 均正常）

---

## 阶段 2 · 记忆层与多轮对话

**目标**：Ledger / Views / Policy 三件套（ADR-003）+ Resolver 白名单 function calling（B14 / ADR-008）+ `POST /api/chat` 多轮约束演进对话（B12，每轮全量重规划）+ 跨会话用户切换演示（B9）。验收口径（§13）：**第 6 轮约束回显正确，Ledger 可回放，跨会话用户隔离可演示**。

- [x] **2.1 记忆表迁移**：`memory_events` / `memory_views`（§9 DDL）SQLAlchemy 模型 + Alembic 迁移（down_revision=12cd442bb18f）✅ 2026-11-17 迁移 `7e23cf1be135` 建两表（11+8 列、supersedes 自引用 FK、confidence/decay_weight server_default 1.0），upgrade 后 PRAGMA 校验与 §9 一致`memory_events` / `memory_views`（§9 DDL）SQLAlchemy 模型 + Alembic 迁移（down_revision=12cd442bb18f）
- [x] **2.2 Ledger 追加写 + Policy**：append（assertion 逐字段 / observation 用户轮次原话）、`supersedes_id` 矛盾归档（不物理删除）、写入边际价值判定（只写本轮显式新出/变化字段）、turn 计数、retract ✅ 2026-11-17 字段→scope 映射（budget/preferences→user，其余→session）；tests 8 项（跳过未变化值、归档后 Ledger 双条可回放、枚举校验）append（assertion 逐字段 / observation 用户轮次原话）、`supersedes_id` 矛盾归档（不物理删除）、写入边际价值判定（只写本轮显式新出/变化字段）、turn 计数、retract
- [x] **2.3 Views 派生与重建**：assertion 写入后增量更新 hard_constraints view（valid_to 关旧、derived_from=event ids）；`rebuild_views()` 清空重放全量 Ledger，前后一致（§13「可回放」）✅ 2026-11-17 consistent 语义 = 已有 view 重建后值不变，缺失 scope 补建记 healed（自愈）；篡改 view → rebuild 报不一致并修复；tests 7 项assertion 写入后增量更新 hard_constraints view（valid_to 关旧、derived_from=event ids）；`rebuild_views()` 清空重放全量 Ledger，前后一致（§13「可回放」）
- [x] **2.4 Memory.read 注入链**：scope 链 session→user→global 过滤 → 时间衰减加权（读时动态算，硬约束不因衰减丢弃）→ 置信度排序 → 结构化硬约束对象 + 逐条 provenance（event_id/turn/source）✅ 2026-11-17 30 天半衰期仅影响排序与展示（守住第 6 轮召回）；显式优先/列表并集/记忆兜底合并；tests 8 项（scope 裁决、跨会话注入、隔离、4 半衰期仍注入）scope 链 session→user→global 过滤 → 时间衰减加权（读时动态算，硬约束不因衰减丢弃）→ 置信度排序 → 结构化硬约束对象 + 逐条 provenance（event_id/turn/source）
- [x] **2.5 LLM 适配器**：`LLMAdapter` Protocol + DeepSeekAdapter（OpenAI-compatible，超时/重试/token 统计，B3 可插拔）+ FastAPI 依赖注入口；tests 用 FakeLLM 脚本化替身（CI 无 key 全确定性）✅ 2026-11-17 错误模型可重试/不可重试、连接类重试一次、arguments_json 原样上抛（护栏职责在 Resolver）；tests 8 项；conftest LLM 哨兵（不可达端点）保证测试进程永不外呼`LLMAdapter` Protocol + DeepSeekAdapter（OpenAI-compatible，超时/重试/token 统计，B3 可插拔）+ FastAPI 依赖注入口；tests 用 FakeLLM 脚本化替身（CI 无 key 全确定性）
- [x] **2.6 Resolver 白名单护栏**：三工具 JSON Schema（`submit_plan_request` / `ask_clarification` / `recall_constraint`）+ 护栏（非白名单拒绝记违规、单次 ≤3 调用、参数 Pydantic 强校验、缺 budget 且无记忆 → 澄清不编造）+ LLM 失败规则模板降级（§7.1）✅ 2026-11-17 `app/llm/tools.py` schema 与 pydantic 模型同构；resolver 记录全部 guardrail_violations 不中断；正则降级解析（预算/万/天数/时长/POI 名/偏好词）；tests 23 项三工具 JSON Schema（`submit_plan_request` / `ask_clarification` / `recall_constraint`）+ 护栏（非白名单拒绝记违规、单次 ≤3 调用、参数 Pydantic 强校验、缺 budget 且无记忆 → 澄清不编造）+ LLM 失败规则模板降级（§7.1）
- [x] **2.7 POST /api/chat SSE 主链路**：observation 落账 → Resolver → Memory.read 权威注入（+ `recall_constraint` 辅助，两路去重合并 B15）→ 全量重规划（solve+verify，与 /api/plan 共用 pipeline）→ Responder（LLM 解释/降级模板）；SSE 事件 start/stage/clarification/delta/done/error；spans 六段；`applied_constraints.constraints[]` 逐字段回显来源与轮次 ✅ 2026-11-17 pipeline 抽取自 plan.py（阶段 1 测试零回归）；POI 名/中文类目归一化护栏（实测 DeepSeek 输出中文偏好）；浏览器实测真实 DeepSeek 两轮对话：预算注入 memory_views#1·turn=1、must_visit 并集增长；tests 7 项observation 落账 → Resolver → Memory.read 权威注入（+ `recall_constraint` 辅助，两路去重合并 B15）→ 全量重规划（solve+verify，与 /api/plan 共用 pipeline）→ Responder（LLM 解释/降级模板）；SSE 事件 start/stage/clarification/delta/done/error；spans 六段；`applied_constraints.constraints[]` 逐字段回显来源与轮次
- [x] **2.8 记忆回放 API + 用户隔离**：`GET /api/memory/events|views`、`POST /api/memory/rebuild`（返回一致性结果）；user scope 跨 session 注入、用户 A 约束不泄给 B（B9）✅ 2026-11-17 u-alice 会话 A 定预算 → 新会话 B 首轮注入（source=memory_views#、turn=1）；u-bob 缺预算触发澄清而非借用；tests 5 项`GET /api/memory/events|views`、`POST /api/memory/rebuild`（返回一致性结果）；user scope 跨 session 注入、用户 A 约束不泄给 B（B9）
- [x] **2.9 前端对话视图**：tab 真切换；用户切换器（u-alice/u-bob）+ 新会话；消息流 + fetch-stream 消费 SSE；行程卡渲染复用；约束来源徽标（memory_views#N / 轮次）✅ 2026-11-17 浏览器实测（IAB）：阶段条→增量文本→行程卡→徽标全链路；第 2 轮徽标「budget=9500 · 记忆 · 第 1 轮」；行程卡渲染抽 `itineraryHtml` 供两视图共用tab 真切换；用户切换器（u-alice/u-bob）+ 新会话；消息流 + fetch-stream 消费 SSE；行程卡渲染复用；约束来源徽标（memory_views#N / 轮次）
- [x] **2.10 一致性验收**：①6 轮演进，第 6 轮 applied_constraints.budget=首轮值、source 指向记忆事件、turn=1（memory_recall@6）②改预算 → supersede 归档，Ledger 两条均可回放 ③rebuild 前后一致 ④用户隔离；阶段 1 回归（check_plans + pytest 全绿）✅ 2026-11-17 六轮演进（预算改值/偏好/加必去/时长/改天数）：第 6 轮 budget=8000（第 3 轮改后值）·turn=3、preferences·turn=2、cap·turn=5 全部正确注入；supersede 链 7000→6500 Ledger 双条可回放；rebuild consistent=True；pytest 106 绿 + ruff clean + check_plans 10/10①6 轮演进，第 6 轮 applied_constraints.budget=首轮值、source 指向记忆事件、turn=1（memory_recall@6）②改预算 → supersede 归档，Ledger 两条均可回放 ③rebuild 前后一致 ④用户隔离；阶段 1 回归（check_plans + pytest 全绿）

**验收**：第 6 轮约束回显正确（source/turn 指向记忆事件）；`POST /api/memory/rebuild` 重放后 views 与增量更新一致；跨会话（同 user 新 session）约束仍注入、切换用户不串扰；`pytest -q` 全绿 + ruff clean；浏览器实测多轮对话与用户切换。　✅ 2026-11-17 全过：六轮演进第 6 轮各约束来源/轮次正确回显（budget 演进链 turn=3、早期约束 turn=2/4/5 不滑点）；rebuild consistent=True（含 healed 补建语义）；u-alice 跨会话注入 vs u-bob 隔离澄清；pytest 106 绿 + ruff clean + check_plans 10/10；浏览器（真实 DeepSeek）实测多轮对话、用户切换、SSE 流式渲染

> **Code review 修复（2026-11-17，fcbcf5f..53da64e 两轴审查后）**：①B15 补真实检索回路——recall_constraint 由运行时查 memory_views 同源值、tool 结果回灌模型、两路合并去重（端到端测试）；②SSE 补 error 事件，异常 run 落 failed、客户端断开落 interrupted；③confidence 补 JSON Schema 数值边界 + schema↔pydantic 契约断言；④列表并集溯源保留记忆侧来源与首提轮次（不被本轮覆写；turn 语义 = 当前值确立轮次，成员首提由 Ledger 可回放）；⑤清理零调用符号、ConstraintEcho 小模型、scope 构造收敛 policy.scope_chain。修后 pytest 111 绿 + ruff clean + check_plans 10/10。
> **已知尾项（延后阶段 3）**：§7.1 降级语义的「最近一次可行方案」未实现（当前降级 = 规则模板解析后重新求解，无 runs 历史回退）——依赖按 session 查询历史 runs 的能力，与 Trace 回放/评测基础设施同批落地更顺。

---

## 阶段 3 · 评测观测

**目标**：40 条全集 + 双基线三列报告（ADR-006）+ Trace 瀑布图 + 评测看板 + CI 回归阻断（§7.3 / §12），并收口阶段 2 遗留尾项「最近一次可行方案」降级。验收口径（§13）：**产出改动前后指标对比**；§12 八指标 TBD 由真实评测回填（§1 数据诚实约定）。

- [x] **3.1 eval_results 表 + response 持久化**：`EvalResult` 模型 + Alembic 迁移（down_revision=7e23cf1be135，§9 DDL 逐列对齐）；`runs` 加可空 `response_json` 列并由 /api/plan、/api/chat 成功路径写入；`app/db/evals.py` 聚合查询（看板数据源） ✅ 2026-11-17 迁移 `a4f9c2d81e07`（upgrade 后 PRAGMA 核对 7 列与 §9 一致、runs 增 response_json）；evals.py 趋势/运行列表/指标名查询；plan 与 chat done 路径均落 response；tests 3 项
- [x] **3.2 评测用例 40 条全集**：32 公开 + 8 hidden（§14 20% 防过拟合）；pydantic 用例 schema（plan 单轮 + **chat 多轮**新格式，turns 内嵌 FakeLLM 脚本供 CI 确定性）；覆盖 §7.3 五类：单约束 / 多约束 / 冲突约束 / 多轮指代（含 6 轮引用首轮预算）/ 不可行；`_meta` 版本头；check_plans 兼容跳过 ✅ 2026-11-17 v2026-11-17.1：32 plan（单约束 4 / 多约束 7 / 冲突 6 / 不可行 6 / 种子 8 + hidden 4）+ 8 chat（公开 6 含 6 轮 recall 回路用例、hidden 2）；chat 用例带固定 start_date（周一闭馆断言不随运行日漂移）；fake 模式 current 列 40/40
- [x] **3.3 app/eval 包**：runner（`python -m app.eval.runner --baseline current|A|B|all`，TestClient + 临时库）/ graders（§12 八指标，断言式评分 ADR-004，tool_success_rate 两类分别统计）/ report（`reports/eval-<sha>.md` 三列 + eval_results 入库 + baseline.json）；基线 B = `MEMORY_INJECTION` 开关关记忆注入；基线 A = 纯 LLM 直出行程过同一套 graders（ADR-006） ✅ 2026-11-17 runner 支持 --real/--fake/--ci/--update-baseline/--json；B 列隔离验证（跨轮记忆用例全数丢 budget→澄清，final 0.844 vs current 1.000）；A 列仅 plan 用例（chat 不适用，报告 N/A）；校准中修出 grader 真缺陷（时间窗冲突未计入 failures）；tests 7 项
- [x] **3.4 「最近一次可行方案」降级**（阶段 2 尾项收口）：`get_last_feasible_run(session_id)`；chat 降级路径（LLM 失败 → 规则模板失败/重规划不可行）回退该 session 最近一次可行方案并标注 degraded + 来源 trace_id；无历史维持澄清 ✅ 2026-11-17 两个钩子点（解析失败 / 重规划不可行）；done.fallback + responder.render span meta 记录来源 trace；回放方案挂当前 trace 下；tests 4 项（回退两分支 / 无历史澄清 / 非降级澄清不回退）
- [x] **3.5 spans 计时精度**：`_span` / pipeline 计时改「墙钟锚点 + perf_counter 精确时长」（Windows 墙钟 ~15.6ms 粒度修复），schema 不变 ✅ 2026-11-17 pipeline 导出 tick()/span_bounds()，chat._span 复用；亚毫秒 span 不再记 0ms（test_span_bounds_precise_duration）
- [x] **3.6 Trace 瀑布图视图**：web 第三视图（占位 tab 激活）——输入 trace_id → `GET /api/runs/{trace_id}` → run 头 + 六段瀑布条（相对归一/状态色/meta 摘要）+ request 回显；纯 CSS 无构建依赖 ✅ 2026-11-17 最近 trace 快选（对话/规划完成自动记录）；span 中文标签 + 时长 + meta 展开 + 请求回显 details；内联 JS node --check 通过
- [x] **3.7 评测看板 + eval API**：`POST /api/eval/run`（返回报告路径）、`GET /api/eval/reports`（按 git_sha 聚合趋势 + 报告列表，§10）；web 第四视图——手写 SVG 趋势折线 + 历史报告表 + 触发评测按钮 ✅ 2026-11-17 router 挂载 main.py；趋势按指标形态（率/求和/均值）绘制，baseline 摘要卡 + 运行列表 + 一键触发；tests 3 项（monkeypatch 契约 + 趋势聚合 + 参数校验）
- [x] **3.8 CI 回归阻断**：ci.yml 加 eval job（公开集 current+B FakeLLM 确定性 → 对比 baseline.json；硬约束指标任一退化或 final_pass_rate 降 >2pp 即失败，§12 验收规则）；`--update-baseline` 显式刷新；分支保护必需检查加 eval ✅ 2026-11-17 eval job（迁移后 `python -m app.eval.runner --ci`，失败上传 reports/ artifact）；本地实测：正常 exit 0 / 抬高基线复现 REGRESSION + exit 1；分支保护已更新为 test+lint+eval（gh api）
- [x] **3.9 真实评测与 TBD 回填**：本地真 key 跑 current/A/B × 40（含 hidden）三列报告入库；project.md §12 八指标 TBD 与 §1 结论数字回填（注明 git_sha / 模型名 / 评测集版本）；README 同步 ✅ 2026-11-17 deepseek-flash · git 7731d7c · 评测集 v2026-11-17.1：**current 39/40（final 0.975）/ A 0/32（0.000——纯 LLM 直出在校验器下平均 5+ 处冲突/例、不可行请求全部硬凑）/ B 32/40（0.800）**；memory_recall@6 0.875 vs B 0.125；时间窗冲突 0 vs A 168。唯一失败用例（chat_six_turn_reference）如实记录：真实 LLM 第 6 轮从对话 history 复读出正确预算值但绕过记忆通道（source=request），暴露真实行为；A 列 P95/cost 本次未计量（计量逻辑已补齐，后续运行生效）。报告 `reports/eval-7731d7c.md`；project.md §1/§12/§13 回填
- [x] **3.10 一致性验收**：pytest 全绿 + ruff clean + check_plans（plan 子集含 hidden）全过 + CI eval 阻断实测 + 浏览器实测瀑布图与看板 ✅ 2026-11-17 pytest 129 绿 + ruff clean；check_plans 32/32 + runner fake 40/40；回归阻断抬高基线复现 exit 1；分支保护 test+lint+eval 生效；浏览器（Playwright + 视觉复核）实测六段瀑布与看板 SVG 趋势/基线卡/运行列表，页面零 JS 错误

**验收**：`python -m app.eval.runner --include-hidden --baseline all` 产出三列报告且 final_pass_rate=current 列全过口径成立；CI eval job 对故意退化 exit 1；`GET /api/eval/reports` 返回多 git_sha 趋势；浏览器出示一条完整 trace 瀑布图（§16 证据）；§12 TBD 全部回填真实数字。　✅ 2026-11-17 全过：三列报告 current 0.975 / A 0.000 / B 0.800（40 条含 hidden，deepseek-flash）；回归阻断实测 exit 1（REGRESSION 消息 + 退出码）；/api/eval/reports 多 sha 趋势（eval_results 入库）；瀑布图六段浏览器出示（含真实 LLM trace）；project.md §12 八指标 + §1 结论数字全部真实回填（数据诚实约定）。**阶段 3 冻结。**

> **Code review 修复（2026-11-18，62217b6..bcb01d3 两轴审查后）**：①graders 口径对齐 §12——budget_accuracy 补「回显一致且不超支」双查、hard_constraint 纯化（days_count/turn_types 等行为类断言只进 final）、去「预算超支」子串反推协议；②§7.3 漏项「坏例一键转评测用例」补齐（app/eval/badcase.py + scripts/badcase_to_eval.py，chat run 从回显终值还原请求，_meta 计数同步 + 重载校验）；③报告评测集 sha256 摘要接通（runner 传 cases_path）；④B 列写侧隔离（session/user 加 :B 后缀，不再向共享 scope 写评测记忆）；⑤A 列 fake 模式改「未运行」呈现而非伪 FAIL；⑥check_plans 断言收敛 grade_plan_case 单一来源；⑦指标标签/聚合形态后端 metrics_meta 下发（前端删硬编码与 mode 级联）；⑧小清理：chat 公开 get_llm_optional 接缝、SSE 解析去重（conftest）、删 dump_results/aggregate_column 中间层、evals 注释失真、前端死分支；⑨文档回写：project.md §9 补 runs.response_json、§10 补 chat 可选 start_date、README 补分支保护复核命令。修后 pytest 133 绿 + ruff clean + fake 40/40 + check_plans 32/32 + --ci exit 0。

---

## 阶段 4 · 平台化

**目标**：Capability Registry（§7.4，SQLite 存权重与版本绑定、路由时读取、回滚不重启）+ 能力级 30 秒心跳健康检查（失败自动摘除）+ solver v1/v2 灰度与一键回滚（ADR-007，差异收敛在 SolverParams 参数层）+ §15 故障注入测试 + 只读 discovery-only Agent Card（ADR-009 / §7.7）。验收口径（§13）：**回滚无需重启；降级路径可用；卡片与 `/api/plan` schema 一致**。

- [x] **4.1 Registry 数据层**：`capabilities` 表（§9 DDL 逐列对齐，PK(name,version)）SQLAlchemy 模型 + Alembic 迁移（down_revision=a4f9c2d81e07）+ 默认能力卡幂等种子（solver@v1/v2、matrix@static、verifier@v1、llm_adapter，§7.1 发现链三件套 + LLM 适配器）；status 语义 `active | unhealthy | retired` ✅ 2026-11-18 迁移 `c5e01a4b7d02`（upgrade 后 PRAGMA 核对 11 列与 §9 一致）；`app/registry/seed.py` 5 张默认卡（solver@v2 权重 0——基线零漂移）；`ensure_seed` 缺行补插、绝不覆写已改行（回滚状态不误复活）
- [x] **4.2 Registry 服务与求解器路由**：`app/registry/`（card pydantic schema、register/deregister/discover、`traffic_weight` 加权路由、set_weight）；路由确定性（同 trace_id 恒同版本，评测集可断言）；pipeline 经 Registry 选 solver 版本（`registry.route` span + `solver.solve` meta 记版本）；solver v2 = SolverParams 参数化（ADR-007 不分叉代码，默认权重 v2=0 保基线零漂移） ✅ 2026-11-18 加权选卡用 sha256(trace_id)（内置 hash 有进程盐，不可用）；v2 = alpha 0.7 / beta 0.05 / w_score 150 / pref_boost 1.0（高分优先取舍）；pipeline 接缝在 solve 前（§7.1 链路位置），matrix/verifier 单版本卡作可用性闸门；tests 12 项（确定性/回滚/摘除接管/幂等种子）
- [x] **4.3 心跳健康检查**：各 kind 确定性健康检查绑定（solver 微缩自检求解 / matrix 加载 / verifier 已知用例 / llm 配置在场，均不外呼）；失败自动摘除（status=unhealthy 路由跳过）、恢复自动回切；FastAPI lifespan（种子→首跳→30s 循环→stale run 清扫）；`/readyz` 增 registry 检查（关键 kind ≥1 张 active 卡） ✅ 2026-11-18 `app/registry/health.py` BINDINGS 五绑定（测试直接调 run_heartbeat，interval 可经 `REGISTRY_HEARTBEAT_INTERVAL_S` 停表防竞态）；readyz 摘除场景实测 503；stale run 清扫 `mark_stale_running_interrupted()`（§7.1 优雅关闭闭环）
- [x] **4.4 Registry API（§10）**：`GET/POST /api/registry/capabilities`（POST=upsert，权重/状态更新即回滚通道——改一行记录不重启）、`GET /api/registry/health`（各能力心跳状态） ✅ 2026-11-18 另加两条 §10 外窄更新辅助端点（`POST .../{name}/{version}/weight|deregister`，只动一个字段避免整卡 upsert 误覆写，同 /api/pois 先例）；tests 6 项
- [x] **4.5 只读 Agent Card（ADR-009/§7.7）**：`GET /.well-known/agent.json` 静态自描述（discovery-only、无 A2A task 端点、`x-tripagent.requestFields` 由 PlanRequest schema 生成）+ nginx 反代白名单 + §15 契约测试（skills[0].id / 与 /api/plan schema 一致 / scope） ✅ 2026-11-18 `app/api/agent_card.py`（requestFields = list(PlanRequest.model_fields)，真生成非手抄；url 取 request.base_url）；nginx 白名单加 `\.well-known/agent\.json` 且 Host 用 $http_host 透传端口（compose 实测 url=http://localhost:3000/api/plan）；契约 tests 7 项（含「不存在 A2A task 端点」负向断言）
- [x] **4.6 故障注入测试（§15）**：LLM 超时→规则模板降级；矩阵能力不可用→503 结构化错误 + run 落 failed + 不硬凑行程；solver v1 心跳失败→自动摘除→流量落 v2→恢复回切；stale running run→启动清扫标 interrupted（§7.1）；/api/plan 补统一错误模型（code/retryable/message） ✅ 2026-11-18 `tests/test_fault_injection.py` 7 项：LLM 超时双失败→done.llm.degraded 且行程仍可行；matrix 摘除→readyz 503 + plan 503 `capability_unavailable` + run failed + spans 末段 registry.route error；数据层异常→500 `internal_error`；v1 心跳崩溃→摘除→20/20 落 v2→绑定恢复→回切；澄清路径不受摘除影响
- [x] **4.7 前端 Registry 视图**：第五 tab——能力卡表格（版本/状态/权重/心跳徽标）+ 权重编辑 + 一键灰度/回滚预设 + 触发规划在瀑布图看 solver 版本切换 ✅ 2026-11-18 浏览器实测（Playwright）：卡片表 5 行 + 心跳摘要（30s · 健康 5）；灰度预设点击后探针路由 v1（目标 7882）→ v2 全量后探针路由 v2（目标 12142，取舍差异可见）；瀑布图 span 标签「能力路由 → 求解 → 独立校验」；页面零 JS 错误
- [x] **4.8 灰度评测断言（ADR-007）**：公开 plan 种子集强制 v2 全量跑 graders 全绿（硬约束不破坏）+ 至少一例 objective/visited 与 v1 不同（证明确有取舍差异） ✅ 2026-11-18 `tests/test_gray_release.py` 3 项：v2 权重 100 下公开 plan 集全过 grade_plan_case；3 日探针 v1/v2 的 objective 与 visited 差异断言（meta.solver_version 核验路由真实指向）；默认权重 10 trace 恒 v1（CI 确定性）
- [x] **4.9 文档回写与一致性验收**：README 阶段 4 块 + 测试数修正 + `MEMORY_INJECTION` 补表（audit P1-1/P2-2）；project.md §11.2/§13 状态/日期回填（P2-2/P3-1）；pyproject ruff `extend-exclude=["alembic/versions"]`（P2-1）；pytest 全绿 + ruff clean + check_plans + eval `--ci` exit 0 + compose 冒烟 + 浏览器实测 ✅ 2026-11-18 audit 六条建议全部收口（README 测试数 169、MEMORY_INJECTION/REGISTRY_HEARTBEAT_INTERVAL_S 补 .env.example 与 §11.2、project.md 日期、ruff 全仓口径 exit 0）；pytest 169 绿（133 既有零回归 + 36 新增）+ ruff clean + check_plans 32/32 + eval --ci exit 0（current 1.000 / B 0.844，基线对比通过）+ compose 冒烟（healthz/readyz 含 registry 检查/agent.json 经 3000 反代）+ 灰度→回滚 curl 全程演示（默认 v1 → v2 全量 → 回滚 v1 → 80/20 混合 7:3，零重启）

**验收**：改 `traffic_weight` 后下一次 `/api/plan` 即切版本（不重启进程，span 可见）；心跳失败能力被摘除、恢复后自动回切、降级路径不硬凑；`/.well-known/agent.json` 与 `/api/plan` 请求 schema 字段一致且声明 discovery-only；`pytest -q` 全绿 + ruff clean + `check_plans` 全过 + eval `--ci` exit 0；浏览器出示 Registry 灰度回滚操作（§16 证据）。　✅ 2026-11-18 全过：compose 环境实测权重改行→请求即切版本（registry.route/solver.solve meta 双核验），回滚仅一行记录零重启；v1 心跳失败摘除→流量 20/20 落 v2→恢复回切（tests 复现）；降级三路（LLM 规则模板 / 能力 503 不硬凑 / 最近可行方案——阶段 3.4）均可用；agent card 契约 7 测试含字段一致性与 task 端点负向断言；pytest 169 + ruff 双口径 + check_plans 32/32 + eval --ci exit 0；浏览器（Playwright）实测第五视图操作与瀑布版本标注。**阶段 4 冻结。**

> **Code review 修复（2026-11-18，53cc2f8..9c980d5 两轴审查后）**：①心跳分发改按卡内 `healthcheck.type`（§9 该列用途本体）——动态注册的卡自动纳入「失败自动摘除」，未知 type 如实报 unknown 不误摘；②§7.4 版本策略落地——`policy_view`（pinned/canary/off + latest 视图，随 GET capabilities 下发）+ `discover(latest_only)`，保守默认新版本权重 0 不自动切 latest；③§7.4 `input_schema` 发现补齐（`discover(input_fields=[])` 字段子集匹配）；④matrix 闸门改按 name（`route_name`）——同 kind 无关卡不再能冒名顶替掩盖摘除；⑤未知 solver 版本拒路由与摘除/摘卡同走 error span 口径；⑥Agent Card 契约测试补**独立来源**对照（解 OpenAPI $ref 取 PlanRequest properties，不再与卡端同源常量恒真比对）；⑦迁移 4 个长文本列 String→Text 对齐 §9；⑧smell 清理：solver 卡从 SOLVER_VERSIONS 派生（新增版本单点改）、HEARTBEAT_INTERVAL_S 死常量删除、plan.py 三处 finish_run 收口、set_status 状态枚举校验、四处权重复位 fixture 收敛 conftest、`__init__` 导出补齐、api 端点 docstring 补全、种子路径对称；⑨文档回写：§7.4 实现口径注记、§7.7 url 动态生成注记、§10 补 weight/deregister 两条辅助端点。修后 pytest 183 绿 + ruff clean + fake 40/40 + check_plans 32/32。

---

## 阶段 5 · 上线与真实使用（2026-11-18 起，一个月）

**目标**：部署上线并投递岗位；此后以真实使用驱动迭代。主打场景 **S2：同一会话内反复斟酌**（约束确定性累积已具备，缺口让 dogfood 暴露）。**不预建功能**——backlog 一律等真实使用证明痛了才做；期间代码可能大改，但阶段主线不变。决议来源：2026-11-18 grilling 会话（与作者逐轮确认）。

- [ ] **5.1 上线与投递（本周）**：0.7 VPS 部署验证（compose on Linux，`/healthz` git_sha，公开 demo URL）；线头收完即**投递 MJ036607**，不等任何新功能 ✅ 2026-11-18 已收（f15e543）：architecture.svg 下线摘链（移本地 docs/）、audit 两份入库
- [ ] **5.2 作者 dogfood**：以旅行者身份 ≥3 个真实 session（含按行程实际出行 1 次）；badcase → `scripts/badcase_to_eval.py` 转用例 → 修复 → eval 回归，闭环至少跑通一轮
- [ ] **5.3 外部真实用户**：外部真人产生 ≥10 个真实 plan（朋友/网友皆可）
- [ ] **5.4 前端美化**：限时 3 天一次性收口，到点即停

**验收**：dogfood ≥3 次（含实际出行 1 次）；外部 ≥10 个真实 plan；前端 3 天内收口；badcase→用例→修复闭环至少一轮。

### Backlog（已定向、未排期；做前再细化，不自动开工）

- **MCP 接高德**（方案 a）：运行时实时获取 + 快照缓存落盘，静态兜底；评测与 CI 锁快照保确定性（B7 不变量以快照形式延续）
- **A2A**（形态：被调用方，Agent Card 为交互契约）：做前先调研现网实现，再定调用方（自写最小 client vs 现成通用 Agent）/ 协议深度（薄版 vs 含 input-required 生命周期，ask_clarification 天然对应）/ 时点
- **S2 深化候选**：变更摘要（「这轮改了什么」）、局部编辑（「只改第二天」——B12 曾明确砍掉，等痛点翻案）
- **S3a trip 作用域**（scope 链 session→trip→user→global，防上一行程预算漏进新行程）、**S3b 用户画像**（跨行程越用越好）、**S4 主动推荐**：依赖回头客与行为数据量，暂缓
