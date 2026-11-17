# TASKS — 阶段 0–3 任务清单

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

**验收**：`python -m app.eval.runner --include-hidden --baseline all` 产出三列报告且 final_pass_rate=current 列全过口径成立；CI eval job 对故意退化 exit 1；`GET /api/eval/reports` 返回多 git_sha 趋势；浏览器出示一条完整 trace 瀑布图（§16 证据）；§12 TBD 全部回填真实数字。

---

## 后续阶段（占位，阶段 3 冻结后细化）

- **阶段 4 · 平台化**：Registry、健康检查、solver v1/v2 灰度回滚（ADR-007）、故障注入测试、只读 Agent Card（ADR-009）
