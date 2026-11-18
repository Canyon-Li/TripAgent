# TripAgent — 多约束行程 Agent 可靠性平台

> 技术设计与实施方案 · 面向携程集团 2027 届秋招 Agent 开发工程师（MJ036607）

| 项目 | 内容 |
| --- | --- |
| 文档状态 | **Draft / 设计稿（边界已确认，见 §0）**（实现进度见 §13） |
| 版本 | v0.3.0 |
| 最后更新 | 2026-11-18 |
| 目标读者 | 项目开发者、面试考官、后续维护者 |
| 关联文件 | `architecture.svg`（总体架构图）、`TASKS.md`（阶段 0–2 任务清单） |
| 技术栈 | Python 3.11+ / FastAPI / SQLite / OR-Tools / OpenAI-compatible LLM / 静态前端 / Docker Compose |

> **数据诚实约定**：文中所有标记为 `TBD` 的指标均为待回填占位。必须由评测脚本在对应阶段真实跑出后写入，未实现前不得填写数字。评审与面试时应能出示评测报告原文和 commit 记录。

---

## 0. 边界确认记录（2026-11-17）

> 开发启动前与作者逐轮确认的实施边界（B1–B17），是 §3 / §6 / §13 的执行口径补充；如与正文冲突，以本节为准。B1–B13 为首轮确认；B14–B17 为同日合并 WorkBuddy 版 PRD 后的二轮确认。

| # | 维度 | 决定 | 影响 |
| --- | --- | --- | --- |
| B1 | 时间盒 | 2026-11-17 起 ≥ 1 个月投入 | 阶段 0–4 全做，阶段 4 不再是"视余力" |
| B2 | 首个里程碑 | 阶段 1「最小可跑行程」 | 开发顺序 0→1→2→3→4，阶段 1 冻结后才进入记忆层 |
| B3 | LLM 供应商 | DeepSeek（OpenAI-compatible，适配器保持可插拔） | 结构化输出走白名单 function calling（B14 / ADR-008）+ JSON Schema 强校验 + 规则模板降级 |
| B4 | 部署 | 自有 VPS，Docker Compose 自托管 | §3.1 G5 成立，公开 demo URL 落在自有域名/IP |
| B5 | 城市 | 北京，18–30 个 POI | 与 §10 请求示例一致 |
| B6 | POI 属性 | 高德 Web 服务 API 一次性导出 + 人工校对（营业时间/门票人工补齐） | 需申请高德 key；README 标注来源与导出日期 |
| B7 | 距离矩阵 | 一次性预计算落盘 `data/matrix.json`（高德驾车优先，OSRM 兜底） | 运行时零外部调用，`ROUTING_PROVIDER=static` 为默认 |
| B8 | 仓库与 CI | GitHub 公开仓 + Actions（pytest + eval 回归） | commit 历史本身即 §16 证据链 |
| B9 | 用户模型 | 2–3 个可切换演示用户，无登录，`session_id` 区分会话 | 跨会话记忆与用户隔离可演示；不做账号体系（§3.2 维持） |
| B10 | 评测基线 | 双基线：A = 纯 LLM 直接生成行程；B = 完整管线关闭记忆注入 | 见 ADR-006 |
| B11 | 灰度载体 | solver v1/v2 双版本（不同目标权重/取舍策略） | 见 ADR-007；确定性结果可用评测集断言 |
| B12 | 对话范围 | 多轮约束演进：每轮全量重规划；不做局部编辑、不做行程问答 | Resolver 只需产出 PlanRequest 与既有约束合并 |
| B13 | 本地开发 | Windows 原生 Python + Docker Desktop；VPS 上 Compose 验证 | 与 CI（ubuntu-latest）行为对齐依赖 Docker |
| B14 | LLM 工具边界 | 白名单 function calling：`submit_plan_request` / `ask_clarification` / `recall_constraint` 三工具，单次请求调用上限 3，非白名单工具名直接拒绝 | ADR-008 / §7.6；取代 B3 原定 JSON mode |
| B15 | 记忆检索双通道 | 固定链路 `Memory.read` 始终执行、为权威注入；`recall_constraint` 仅辅助检索显式历史引用，返回值同源 `memory_views`，两路合并去重 | §7.2 / §7.6 |
| B16 | 协议边界 | 不实现 A2A / MCP 协议栈与跨服务任务状态机；仅发布只读 discovery-only Agent Card | ADR-009 / §7.7 |
| B17 | Agent 框架 | 不引入 LangGraph 等编排框架，白名单用 OpenAI-compatible `tools` 参数原生实现 | 保持自研可插拔运行时叙事（JD ②），§17 依赖清单不变 |

---

## 1. 摘要（TL;DR）

本项目以一个**多约束多日行程规划 Agent** 为业务载体，构建其运行所需的四层 Agent 基础设施：**记忆系统、评测与观测、注册与发现、框架运行时**。业务侧用确定性约束求解保证行程可执行，基础设施侧保证 Agent 在多轮对话、版本迭代和组件故障下仍然可靠。

设计动机来自岗位 JD 的职责分布：五条职责中仅第 1 条是 Agent 应用开发，其余四条（框架稳定性、记忆一致性、评测观测、注册管理发现）均为平台工程。因此项目不以"生成一份好看的攻略"为目标，而以"一个能上线、可评测、可回滚的 Agent 系统"为目标。

**一句话可验证结论**：在 40 条带标准答案的评测集上，加入记忆层与独立校验器后，第 6 轮对话的硬约束召回率与行程可执行率相对双基线（A：纯 LLM 直接生成；B：完整管线关闭记忆注入，见 ADR-006）提升——**final_pass_rate：current 0.975 vs A 0.000（+97.5pp）vs B 0.800（+17.5pp）；memory_recall@6：0.875 vs B 0.125（+75pp）；时间窗冲突 0 vs A 168**（2026-11-17 实测，deepseek-flash，[报告](reports/eval-7731d7c.md)），且每次代码改动都能自动产出回归对比报告（CI `eval` job）。

---

## 2. 背景与动机

### 2.1 岗位职责对齐

| JD 职责 | 项目对应模块 | 可出示的证据 |
| --- | --- | --- |
| ① Agent 应用工程开发，完成 AI 产品上线 | 行程规划 Agent + 静态前端 + 公开 demo | 可访问 URL、Docker Compose 一键启动、README |
| ② Agent 基础框架建设，稳定性与扩展性 | 可插拔运行时、LLM 适配器、降级策略 | 组件接口定义、故障注入测试、优雅重启记录 |
| ③ 记忆系统设计，保证上下文一致性 | Raw Ledger + Views + Policy | 多轮一致性演示、约束召回率评测曲线 |
| ④ Agent 评测、观测能力建设 | 断言式评测集、Trace Store、回归看板 | `reports/eval-*.md` 指标对比、trace 瀑布图 |
| ⑤ Agent 注册、管理、发现架构 | Capability Registry、健康检查、灰度路由、只读 Agent Card | 能力卡片列表、灰度/回滚演示、`/.well-known/agent.json` |

加分项对齐：**独立完成原型到上线**（§11 部署、§13 路线图）、**前后端经验 + 系统性思考**（静态前端 + 分层架构 + ADR 决策记录）。

### 2.2 业务问题陈述

携程自有行程 Agent（TripGenie）公开资料显示其正在建设长期记忆能力；第三方长期使用测评指出两类典型失效：

1. **多轮上下文滑点**：对话超过五六轮后，早期提出的硬约束（如"预算一万"）会被遗漏。
2. **行程单幻觉**：生成结果中出现已关闭景点或不合理的交通耗时。

二者根因相同：**把事实与约束交给 LLM 自由生成，而没有确定性校验与可追溯状态**。本项目的核心命题是让 LLM 负责"理解与解释"，让求解器与数据库负责"事实与约束"。

---

## 3. 目标与非目标

### 3.1 目标（Goals）

- G1：单城 3 日行程，硬约束（营业时间窗、停留时长、每日时长上限、总预算、酒店往返）100% 由求解器与校验器保证。
- G2：跨会话、跨多轮的约束记忆可追溯、可撤销、可衰减，第 6 轮仍能正确注入首轮约束。
- G3：每次代码变更自动运行评测集，输出可比较的量化报告，指标退化可阻断合并。
- G4：能力组件可注册、可发现、可按版本灰度与回滚，回滚不需要重启服务。
- G5：从原型到上线全流程可复现（`docker compose up` + 公开 demo + CI）。

### 3.2 非目标（Non-goals）

- 不实现真实机票/酒店库存与支付；POI 与价格使用可公开的静态数据集。
- 不做多 Agent 协同编排、不实现 A2A / MCP 协议栈与跨服务任务状态机、不做分布式调度、不自研向量数据库（边界论证见 ADR-009 与 §7.7）。
- 不做模型训练或微调；LLM 仅通过 OpenAI-compatible 接口调用。
- 不追求全网城市覆盖，评测与演示限定在 1 个城市、18–30 个 POI。
- 不引入 Kafka / Redis / PostgreSQL 等中间件，除非 §14 风险触发。

> 非目标与边界同样是设计成果，面试中主动说明边界比堆功能更能体现系统性思考。

---

## 4. 术语表

| 术语 | 定义 |
| --- | --- |
| **Hard constraint** | 必须满足的约束，违反即判定该次计划失败 |
| **Ledger** | 追加写、不可变的记忆事件账本，唯一权威事实来源 |
| **View** | 由 Ledger 派生的可检索状态（用户画像、硬约束集），可重建 |
| **Provenance** | 记忆条目的来源（用户明示 / 行为推断 / 环境观测 / Agent 生成） |
| **Capability card** | 描述一个可调用能力的注册条目（schema、版本、成本、健康检查） |
| **Function calling** | LLM 通过 `tools` / `tool_calls` 输出结构化工具调用；本项目仅用于边界处的意图解析（ADR-008） |
| **Capability 调用** | 运行时代码按固定链路调用 Registry 中已注册能力，不由模型决策（区别于上者） |
| **MCP** | Model Context Protocol，标准化 Agent ↔ 工具 / 数据源的连接（AAIF / Linux Foundation） |
| **A2A** | Agent2Agent Protocol，标准化独立 Agent ↔ 独立 Agent 的发现与任务委派（v1.0，Linux Foundation / AAIF） |
| **Agent Card** | A2A 中位于 well-known 路径的 JSON 自描述文档，声明身份、skills、端点与鉴权；本项目仅发布只读版本（ADR-009） |
| **Trace** | 一次请求内所有 span 的集合，用 `trace_id` 贯穿 |
| **Final pass rate** | 所有适用断言同时通过的用例占比，非单项平均 |
| **Drop penalty** | OR-Tools Routing 中"不访问该节点"的惩罚，用于表达景点价值 |

---

## 5. 总体架构

完整架构图见 **`architecture.svg`**（同目录，可直接被 Markdown / 浏览器 / 简历引用）。

系统分六层，自上而下：

1. **接入层**：静态单页，三个视图 —— 行程对话、Trace 回放、评测看板。
2. **API 层**：FastAPI 网关，统一暴露对话、求解、运行记录、评测、注册中心与健康检查端点。
3. **Agent 运行时**：意图解析 → 记忆检索 → 能力发现 → 规划求解 → 约束校验与应答，单向主链路。
4. **基础设施层**：四个并列支柱（记忆 / 评测观测 / 注册发现 / 框架稳定性），分别对应 JD ②③④⑤。
5. **能力与数据层**：CP-SAT/Routing 求解器、距离矩阵服务、SQLite、LLM 适配器。
6. **外部依赖**：LLM 供应商、OSRM 或高德距离矩阵、Docker 与 CI。

**关键设计原则**：主链路单向且无环；基础设施全部旁路接入，不侵入业务逻辑；所有事实类数据只从数据层读取，LLM 输出在进入求解器前必须结构化。

---

## 6. 关键设计决策（ADR）

### ADR-001：LLM 不参与时间排程

- **状态**：Accepted
- **上下文**：LLM 生成的时间线无法保证营业时间窗与交通耗时一致性。
- **备选**：A) LLM 直接生成行程；B) LLM 生成后由规则校验并重试；C) LLM 只做意图解析，求解器排程。
- **决定**：C。LLM 输出结构化 `PlanRequest`（城市、天数、预算、偏好、必去点），OR-Tools 求解，独立校验器复核。
- **后果**：行程幻觉从"模型问题"变为"可测试的代码问题"；代价是需要维护结构化抽取的提示词与兜底模板。

### ADR-002：使用 OR-Tools Routing 而非纯 CP-SAT `AddCircuit`

- **状态**：Accepted
- **上下文**：需要时间窗、预算维度、景点取舍（可选访问）。
- **决定**：采用 `RoutingModel` 建 VRPTW：m 天 = m 辆车，时间窗用 `AddDimension`，景点取舍用 `AddDisjunction` + drop penalty = 景点评分。若后续需要"同行者分合流 / 房间容量"等复杂逻辑，再引入 CP-SAT 作为第二求解器（见 TripChord 的求解器组合思路）。
- **后果**：开发量小、开箱即用的时间窗维度；代价是目标函数受 Routing 的"最小化"形式约束，需把"最大化评分"转换为"不选的惩罚"（§8）。

### ADR-003：记忆采用 Ledger / Views / Policy 三件套，而非单一向量库

- **状态**：Accepted
- **上下文**：需要可溯源、可撤销、可衰减、可重建，且必须区分"用户明示"与"Agent 推断"。
- **决定**：SQLite `memory_events` 作权威账本；`memory_views` 为派生视图；读写遗忘由显式 Policy 控制并记录为 Action 序列。
- **后果**：满足可审计与一致性；代价是比向量库多一层写入判定，短期不追求语义模糊召回。

### ADR-004：评测采用断言式评分，不用 LLM-as-judge 作为唯一依据

- **状态**：Accepted
- **上下文**：JD 要求评测能力建设，且指标必须可复现。
- **决定**：硬约束、时间窗、预算、记忆召回、工具调用均由确定性脚本判定；LLM 仅用于生成描述的流畅度打分（可选、不参与 `final_pass_rate`）。
- **后果**：回归结果稳定可比；代价是需要人工构造 40 条带标准答案的用例。

### ADR-005：SQLite + 静态前端，不引入中间件

- **状态**：Accepted
- **上下文**：单人项目、需快速上线、部署可复现。
- **决定**：SQLite（WAL 模式）+ 单进程 FastAPI + Nginx 托管静态页；重启状态从 `runs` 表恢复。
- **后果**：一键启动、零运维；代价是并发写入受限（§14 有触发升级的条件）。

### ADR-006：评测采用双基线对比

- **状态**：Accepted（2026-11-17，边界确认 B10）
- **上下文**：§1 的"相对基线提升"（阶段 3 实测：current 0.975 vs A 0.000 vs B 0.800）需要明确参照物；单一基线无法同时支撑"约束可靠性"与"记忆滑点"两个叙事。
- **备选**：A) 仅纯 LLM 基线（差距大但说不清记忆层贡献）；B) 仅无记忆基线（严谨但缺少"为什么不让 LLM 排程"的直接证据）。
- **决定**：双基线。基线 A = 纯 LLM 直接生成行程（复现 TripGenie 式失效）；基线 B = 完整管线但关闭记忆注入（隔离记忆层贡献）。评测报告同时呈现 current / A / B 三列。
- **后果**：可拆分"求解器+校验器贡献"（对比 A）与"记忆层贡献"（对比 B）；代价是评测运行次数 ×3，token 成本相应增加。

### ADR-007：Registry 灰度演示以求解器双版本为载体

- **状态**：Accepted（2026-11-17，边界确认 B11）
- **上下文**：§7.4 的 `traffic_weight` 灰度需要至少一个组件存在两个可路由版本；LLM 适配器输出有随机性、verifier 双版本需故意造缺陷，演示均不稳定。
- **备选**：A) LLM 适配器灰度；B) 校验器双版本；C) 求解器双版本。
- **决定**：C。solver v1/v2 以相同接口注册（差异收敛在目标权重/取舍策略参数层），按 `traffic_weight` 分流，回滚即改权重绑定。
- **后果**：灰度效果可用确定性评测集直接断言，演示可复现；代价是需维护两版求解器入口（参数化实现，避免代码分叉）。

### ADR-008：Function calling 只用于边界，不用于编排

- **状态**：Accepted（2026-11-17，边界确认 B14）
- **上下文**：主流 Agent 框架鼓励 LLM 自主选工具（ReAct / tool loop）。但本项目核心命题是让 LLM 负责理解与解释、求解器负责事实与约束；若开放自主工具循环，模型可能漏调校验器、重复调用求解器，或把不可靠事实写进参数。
- **备选**：A) 暴露全部能力，由 LLM 自主 tool loop；B) LLM 只输出纯文本或 JSON mode 结果，运行时解析；C) LLM 用 function calling 输出结构化意图与少量白名单工具，运行时按固定链路调度其余能力。
- **决定**：C。LLM 可见的工具仅 3 个白名单：`submit_plan_request`、`ask_clarification`、`recall_constraint`。求解器、距离矩阵、校验器、记忆写入等能力**不暴露给 LLM**，由运行时按 §7.1 固定顺序强制调用。白名单用 OpenAI-compatible `tools` 参数原生实现，不引入编排框架（B17）。
- **后果**：LLM 侧获得原生 function calling 的类型约束与可观测性，同时主链路保持确定性、可复现、可评测；代价是放弃"模型自主规划工具序列"的灵活性，新增能力需改运行时链路而非只改提示词。
- **关联**：与 ADR-001 一致；LLM 工具与 capability 调用的区分见 §7.6。

### ADR-009：不实现 A2A 运行时，只发布只读 Agent Card

- **状态**：Accepted（2026-11-17，边界确认 B16）
- **上下文**：JD 第 5 条要求"Agent 注册、管理、发现架构"。A2A（Agent2Agent，Google 2025-04 发布、2025-06 捐给 Linux Foundation、2026 年发布 v1.0 稳定版，现归属 AAIF）用 Agent Card 做能力发现，与本项目的 Registry 高度同构，容易被误认为应当直接接入。但 A2A 面向的是**跨团队 / 跨组织、对端同样是自主 Agent** 的委托协作，其核心是 Task 状态机（`submitted → working → input-required → completed / failed / canceled`）、消息与制品交换、SSE 流式、鉴权与签名卡片。
- **备选**：A) 完整实现 A2A server（Task 生命周期 + `message/send` + SSE + 跨服务鉴权）；B) 完全不提 A2A，只保留进程内 Registry；C) 保留进程内 Registry，另外导出一张**只读、不可委派**的 A2A 风格 Agent Card。
- **决定**：C。主链路仍是 §7.1 的单 Agent 固定链路；求解器、距离矩阵、校验器是确定性服务（固定 schema、无 LLM 规划），按协议分工应保持普通 REST / 进程内调用，不套 Agent ↔ Agent 协议。`GET /.well-known/agent.json` 仅返回静态自描述，卡片内显式声明 `discovery-only` 且不提供 A2A task 端点。
- **后果**：用约 1 个端点 + 1 份 JSON + 1 个契约测试，换来 JD ⑤ 的"发现"可被外部读取，并能在面试中讲清"何时该用 / 不该用 A2A"；不引入 SDK、跨服务鉴权、状态机与重试语义，主链路仍可复现、评测仍确定性。代价是**不声称 A2A 协议完整合规**，必须在卡片与 README 中如实标注。
- **触发升级条件**（满足全部才重新评估 A）：出现第二个由不同团队拥有、需自主澄清与长任务状态的 Agent（如独立机票 / 酒店 Agent）；存在跨组织信任边界与委托鉴权需求；评测集开始覆盖跨 Agent 委派链路。在此之前引入 A2A 属于过度设计。
- **关联**：与 ADR-008（不在编排层引入不确定性）、ADR-005（单机无中间件）一致。

---

## 7. 模块设计

### 7.1 Agent 运行时（职责①②）

统一组件接口（`Protocol`）：`Planner` / `Memory` / `Registry` / `Solver` / `Verifier` / `Responder`，通过依赖注入组装，配置可切换实现。

主链路（单次 `/api/chat`）：

```
用户输入
  → Resolver: LLM 通过白名单 function calling 输出结构化 PlanRequest（含置信度；缺失字段触发 ask_clarification，见 §7.6）
  → Memory.read(scope=session): 注入历史硬约束，冲突时按 provenance 裁决
  → Registry.discover(tags): 找到 solver / matrix / verifier 的当前版本
  → Solver.solve(PlanRequest + 距离矩阵): 输出候选行程
  → Verifier.check(行程): 重算时间线与预算，返回 violations[]
  → 若有 violation：降级重试（放宽软约束）或返回明确的不可行原因
  → Responder: LLM 生成解释文本，前端渲染行程卡 + trace_id
```

稳定性要求：每步有超时与幂等键；LLM 调用失败时降级到"规则模板 + 最近一次可行方案"；`/healthz` 暴露 git sha 与版本；优雅关闭时把未完成的 run 标记为 `interrupted`，重启后可续跑。

### 7.2 记忆系统（职责③）

三条链路：

- **写入**：不是"有价值就存"，而是带预算的边际价值判定。优先写入：用户明示的硬约束、与既有信念冲突的新信号、被重复验证的行为模式。每条记录带 `source / scope / confidence / occurred_at / supersedes_id`。
- **读取**：按 `scope` 过滤 → 时间衰减加权 → 置信度排序 → 输出**结构化硬约束对象**（不是自然语言摘要），直接注入 `PlanRequest`；同时在 system prompt 中做一次约束重述。固定链路的 `Memory.read` 为权威注入通道；模型可通过白名单工具 `recall_constraint` 触发辅助检索，两路结果合并去重（B15 / §7.6）。
- **管理（遗忘）**：时间衰减 + 矛盾归档（新条目 `supersedes_id` 指向旧条目，不物理删除）+ 定期重放 Ledger 重建 Views。

**一致性保证**：硬约束以结构化对象同时传给求解器与校验器，不依赖模型"记住"；对话超过 N 轮触发自动摘要与约束重述，避免上下文窗口滑点。

### 7.3 评测与观测（职责④）

- **评测集**：`data/eval_cases.jsonl`，40 条，覆盖单约束 / 多约束 / 冲突约束 / 多轮指代（第 6 轮引用首轮预算）/ 不可行请求。
- **断言**：硬约束满足、时间窗零冲突、预算不超、指定景点覆盖、记忆召回正确、工具调用成功、响应延迟与成本。
- **观测**：`runs` / `spans` 表记录每次 LLM 调用、检索、求解、校验的耗时与 token；前端按 `trace_id` 渲染瀑布图。
- **回归闭环**：CI 中执行 `eval runner` → 生成 `reports/eval-<sha>.md` → 与 `baseline` 比较 → 指标退化超过阈值即失败；线上坏例可一键转为新评测用例。

### 7.4 注册与发现（职责⑤）

Capability card（§10 有 schema）。Registry 提供：注册 / 注销、按 `tags` + `input_schema` 发现、30 秒心跳健康检查、失败自动摘除、`traffic_weight` 灰度、`pinned | canary | latest` 版本策略。

**实现口径（2026-11-18，阶段 4）**：版本策略三态落在 `traffic_weight` 一维上，由 `policy_view` 按当前权重判定并随 `GET /api/registry/capabilities` 下发——权重全量绑定单一版本 = `pinned`（回滚即重新 pin），多版本分权重 = `canary`，无承接版本 = `off`；`latest` 一栏始终给出各 name 版本序最高的 active 卡（`discover(latest_only=True)` / `?latest=true`），新版本以权重 0 注册、由人放量——保守默认，不自动切 latest。`input_schema` 发现以字段子集匹配实现（`discover(input_fields=[...])`）。心跳分发依据是卡内 `healthcheck.type` 字段（§9 该列的用途本体），动态注册的卡用已知 type 即自动纳入「失败自动摘除」。

**回滚不重启**：灰度权重与版本绑定存在 SQLite，路由时读取；回滚只需更新一行记录。这一设计把"重启与版本管理"从运维杂活变成可演示的平台能力。

### 7.5 框架稳定性（职责②）

配置化 LLM 供应商（`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`），统一适配器；统一错误模型（可重试 / 不可重试 / 降级）；全链路 `trace_id`；SSE 流式输出；Alembic 管理 schema 迁移，保证升级不丢 Ledger。

### 7.6 Function calling 与工具边界（职责②④⑤）

本项目有两类性质不同的"工具调用"，必须区分清楚 —— 这是面试高频追问点，也是很多候选人会混的地方：

| 维度 | LLM 侧 function calling | 运行时侧 capability 调用 |
| --- | --- | --- |
| 触发者 | 模型（`tool_calls`） | 运行时代码（固定链路） |
| 工具来源 | 3 个白名单，写死在请求 `tools` 参数 | Registry 的 capability card，可动态注册 |
| 目的 | 把自然语言转成结构化 `PlanRequest`、请求澄清、引用历史约束 | 执行 POI 查询、距离矩阵、求解、校验 |
| 是否可跳过 | 由模型决定是否调用 `ask_clarification` | 不可跳过，校验器为强制步骤 |
| 版本治理 | 随提示词 / schema 版本发布 | `traffic_weight` 灰度、回滚不重启 |

**白名单工具（OpenAI-compatible `tools` JSON Schema，ADR-008 / B14）**：

- `submit_plan_request(city, days, budget, daily_minutes_cap, must_visit[], preferences[], confidence)`
- `ask_clarification(field, reason)` —— 缺关键字段时不允许模型自行编造预算或天数
- `recall_constraint(key)` —— 用户说"还是按上次那个预算"时显式触发记忆检索，返回值来自 `memory_views`，不是模型回忆；与固定链路 `Memory.read` 合并去重，后者始终执行且为权威（B15）

**护栏**：所有 `tool_calls` 参数用 JSON Schema / Pydantic 强校验；不在白名单内的工具名直接拒绝并记为一次违规；单次请求工具调用次数上限 3 次防循环；每个调用带幂等键与超时；失败走 §7.5 降级；调用名、参数摘要、耗时、状态写入 `spans`，由 `tool_success_rate`（两类分别统计）与 trace 瀑布图观测。

**为什么不让 LLM 直接调用求解器**：求解结果必须可复现，且校验器必须强制执行。把这两步交给模型决策会破坏 ADR-001 的可测试性，也会让 `final_pass_rate` 失去意义。Registry 的能力卡片虽然长得像 function calling 的 schema，但它是给**运行时路由器**用的，不是给模型看的 tool list —— 这个区别正是 JD 第 5 条"注册、管理、发现"与第 1 条"应用开发"的分界。

### 7.7 协议边界与 Agent Card 导出（职责⑤）

三种机制名字相近但连接对象完全不同，混用是这个岗位面试里最常见的失分点：

| 机制 | 连接对象 | 对端是否自主决策 | 本项目状态 | 落地位置 |
| --- | --- | --- | --- | --- |
| Function calling | 模型 → 结构化输出 | 否（模型只填参数） | **已用**：3 个白名单工具 | §7.6 / ADR-008 |
| MCP | Agent → 工具 / 数据 | 否（服务端执行固定能力） | **不引入**：能力在进程内由 Registry 路由 | §7.4 |
| A2A | Agent → Agent | 是（对端自行规划并返回 Task 制品） | **不实现协议栈**：仅发布只读 Agent Card | ADR-009 / 本节导出 |

**只读 Agent Card**：`GET /.well-known/agent.json` 返回静态 JSON，声明本 Agent 的身份、一个 `plan_itinerary` skill、输入输出模式与调用入口。字段为 A2A **风格**示意，不声称完整合规（版本与字段以 https://a2a-protocol.org 官方 spec 为准）：

```json
{
  "name": "TripAgent",
  "description": "多约束多日行程规划；硬约束由确定性求解器与独立校验器保证",
  "version": "0.3.0",
  "url": "https://<demo-host>/api/plan",
  "capabilities": { "streaming": false, "pushNotifications": false },
  "defaultInputModes": ["application/json"],
  "defaultOutputModes": ["application/json"],
  "skills": [
    {
      "id": "plan_itinerary",
      "name": "多约束行程规划",
      "description": "输入城市 / 天数 / 预算 / 必去点，返回逐日时间线与 violations[]",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    }
  ],
  "x-tripagent": {
    "scope": "discovery-only",
    "a2aTaskEndpoint": null,
    "note": "本服务不实现 A2A Task 状态机；请改用 POST /api/plan"
  }
}
```

**明确不做**：不实现 `message/send`、Task 状态机、SSE 制品流、gRPC binding、签名 Agent Card，也不实现主动调用其他 Agent 的 A2A client；不引入 `a2a-sdk` 等依赖。卡片由 `/api/plan` 的请求 schema 生成，二者字段必须一致（§15 契约测试守住——含与服务端 OpenAPI 实际 schema 的独立对照）。实现注记：`url` 字段按请求 host 动态生成（反代后仍指向正确入口，nginx 需透传 `Host: $http_host`），其余字段静态。

**为什么值得做这一张卡片**：JD 第 5 条用的词是"Agent 注册、管理、发现架构"，与 A2A 的 Agent Card 直接呼应。能拿出一张可被外部读取的发现文档、同时说清"为什么没有进一步实现协议"，比只做一个进程内函数调用表更贴近平台工程，也比硬套一个用不上的协议栈更可信。

**为什么求解器不套 A2A**：A2A 的对端应当是"会自行规划、会反问澄清、可能运行很久"的 Agent；本项目求解器是固定输入固定输出的确定性服务，普通 REST / 进程内调用即可。协议层的通行判断也是：对端若只是固定 schema 且无 LLM 规划，就保持普通 HTTP API，A2A 的卡片加任务机制属于纯开销。

---

## 8. 约束求解建模

**输入**：POI 集合 `i ∈ 1..n`，每个含 `score_i`、`duration_i`、`[open_i, close_i]`、`ticket_i`；天数 `m`；酒店 `H`；每日时长上限 `T_day`；总预算 `B`；时长矩阵 `t[i][j]` 与交通费矩阵 `f[i][j]`。

**模型（OR-Tools Routing）**：

- `m` 辆车，每辆代表一天，起点与终点均为酒店 `H`，`SetFixedCostOfVehicle` 控制是否启用该天。
- 时间维度：`AddDimension(transit_callback, slack_max=T_day, capacity=T_day, fix_start_cumul_to_zero=False, "Time")`；对每个 POI 调 `CumulVar(node).SetRange(open_i, close_i - duration_i)`。
- 预算维度：`AddDimensionWithVehicleCapacity(cost_callback, 0, [B]*m, True, "Budget")`，把门票与交通费计入同一累计维度。
- 景点取舍：`AddDisjunction([node], penalty=score_i * W)`，未被访问的节点付出等于其价值的惩罚。
- 目标（Routing 最小化）：

```
minimize  Σ travel_time(i,j) * α
        + Σ ticket_i * β
        + Σ (score_i * W) * (1 - x_i)
```

其中 `x_i` 为是否被访问。等价于"最大化选中景点总评分 − 时间与花费"，`W` 控制取舍倾向，`α / β` 为归一化系数。

**硬约束**：时间窗、每日时长、总预算、每点至多一次、酒店起终点、天数上限。
**软约束**：每日景点数下限、天数间节奏平衡（后处理或软惩罚，不进入 `final_pass_rate`）。

**独立校验器**（`Verifier`，与求解器完全解耦）：从最终路线重新逐段累加时间、核对闭馆时间、重算门票与交通费、检查重复访问与预算，输出 `violations[]`。**校验器不复用求解器代码路径**，用于捕获建模错误。

---

## 9. 数据模型（SQLite DDL 摘要）

```sql
CREATE TABLE memory_events (
  id            INTEGER PRIMARY KEY,
  session_id    TEXT NOT NULL,
  user_id       TEXT NOT NULL,
  event_type    TEXT NOT NULL,          -- assertion | belief | decision | observation
  content_json  TEXT NOT NULL,
  source        TEXT NOT NULL,          -- user_stated | behavior | environment | agent_generated
  scope         TEXT NOT NULL,          -- trip:<id> | user:<id> | global
  confidence    REAL NOT NULL DEFAULT 1.0,
  decay_weight  REAL NOT NULL DEFAULT 1.0,
  supersedes_id INTEGER REFERENCES memory_events(id),
  created_at    TEXT NOT NULL
);

CREATE TABLE memory_views (
  id              INTEGER PRIMARY KEY,
  view_type       TEXT NOT NULL,        -- hard_constraints | persona | summary
  subject_key     TEXT NOT NULL,
  value_json      TEXT NOT NULL,
  derived_from    TEXT NOT NULL,        -- event id list, for rebuild
  valid_from      TEXT NOT NULL,
  valid_to        TEXT,
  last_confirmed_at TEXT
);

CREATE TABLE capabilities (
  name            TEXT NOT NULL,
  version         TEXT NOT NULL,
  kind            TEXT NOT NULL,        -- tool | solver | verifier | llm_adapter
  input_schema    TEXT NOT NULL,
  output_schema   TEXT NOT NULL,
  endpoint        TEXT NOT NULL,
  healthcheck     TEXT NOT NULL,
  cost_estimate   REAL,
  tags            TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',
  traffic_weight  INTEGER NOT NULL DEFAULT 100,
  PRIMARY KEY (name, version)
);

CREATE TABLE runs (
  trace_id     TEXT PRIMARY KEY,
  session_id   TEXT NOT NULL,
  request_json TEXT NOT NULL,
  response_json TEXT,                  -- 阶段 3：最终 PlanResponse（§7.1 最近一次可行方案回退 + 坏例转用例）
  status       TEXT NOT NULL,          -- running | succeeded | failed | interrupted
  model        TEXT,
  git_sha      TEXT,
  started_at   TEXT NOT NULL,
  finished_at   TEXT,
  latency_ms   INTEGER,
  token_in     INTEGER,
  token_out    INTEGER
);

CREATE TABLE spans (
  id         INTEGER PRIMARY KEY,
  trace_id   TEXT NOT NULL REFERENCES runs(trace_id),
  parent_id  INTEGER,
  name       TEXT NOT NULL,
  start_ms   INTEGER NOT NULL,
  end_ms     INTEGER NOT NULL,
  status     TEXT NOT NULL,
  meta_json  TEXT
);

CREATE TABLE eval_results (
  id          INTEGER PRIMARY KEY,
  git_sha     TEXT NOT NULL,
  case_id     TEXT NOT NULL,
  metric      TEXT NOT NULL,
  passed      INTEGER NOT NULL,
  value       REAL,
  created_at  TEXT NOT NULL
);
```

启用 `PRAGMA journal_mode=WAL;` 与 `foreign_keys=ON;`。所有时间字段用 UTC ISO-8601 字符串。

---

## 10. API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | SSE 流式对话，返回增量文本与最终 `trace_id`。请求体可带可选 `start_date`（评测用例固定日期保证周一闭馆类断言确定性，§15；生产前端不传，默认当天） |
| `POST` | `/api/plan` | 结构化输入直接求解，跳过意图解析（便于评测） |
| `GET` | `/api/runs/{trace_id}` | 返回 run + spans，供 Trace 视图回放 |
| `POST` | `/api/eval/run` | 触发全量评测，返回报告路径 |
| `GET` | `/api/eval/reports` | 列出历史报告与指标趋势 |
| `GET` / `POST` | `/api/registry/capabilities` | 查询 / 注册能力卡片（GET 支持 kind/tag/latest 发现参数，响应含 `policy_view` 版本策略视图） |
| `GET` | `/api/registry/health` | 各能力心跳状态 |
| `POST` | `/api/registry/capabilities/{name}/{version}/weight` | 窄更新辅助端点（阶段 4）：只改一行 `traffic_weight`——灰度/回滚通道，避免整卡 upsert 误覆写 |
| `POST` | `/api/registry/capabilities/{name}/{version}/deregister` | 窄更新辅助端点（阶段 4）：注销（status→retired 归档） |
| `GET` | `/api/memory/events?session_id=&user_id=&scope=` | Ledger 事件回放（追加序，可追溯 supersedes 链） |
| `GET` | `/api/memory/views?session_id=&user_id=` | scope 链当前活跃 hard_constraints 视图（Memory.read 同源） |
| `POST` | `/api/memory/rebuild` | 重放全量 Ledger 重建 Views，返回一致性结果 |
| `GET` | `/healthz` / `/readyz` | 存活与就绪探针（含版本与 git sha） |
| `GET` | `/.well-known/agent.json` | 只读 Agent Card（A2A 风格自描述，discovery-only，见 §7.7） |

请求示例：

```json
POST /api/plan
{
  "city": "beijing",
  "days": 3,
  "hotel": "wangfujing",
  "budget": 10000,
  "daily_minutes_cap": 480,
  "must_visit": ["forbidden_city"],
  "preferences": ["history", "food"],
  "session_id": "s-001"
}
```

响应示例（精简）：

```json
{
  "trace_id": "tr-8f2c...",
  "status": "feasible",
  "days": [
    { "date_index": 0, "stops": [
      { "poi": "forbidden_city", "arrive": "09:00", "leave": "11:30", "wait_min": 0 }
    ] }
  ],
  "applied_constraints": { "budget": 10000, "source": "memory_views#17", "turn": 1 },
  "verification": { "violations": [], "recomputed_budget": 8620 },
  "cost": { "latency_ms": 0, "token_in": 0, "token_out": 0 }
}
```

`applied_constraints` 明确回显约束来源与首次提出的轮次 —— 这是"记忆一致性"最直观的可视化证据。

---

## 11. 部署与运行

### 11.1 目录结构

```
.
├── project.md                 # 本文档
├── architecture.svg           # 总体架构图
├── README.md                  # 快速上手 + 面试导航
├── docker-compose.yml         # api / web / (可选) worker
├── .env.example               # 不含真实密钥
├── pyproject.toml
├── alembic/                   # schema 迁移
├── app/
│   ├── main.py                # FastAPI 入口
│   ├── config.py
│   ├── api/                   # 路由层
│   ├── runtime/               # Planner / Resolver / Responder / 组件接口
│   ├── memory/                # Ledger / Views / Policy
│   ├── registry/              # Capability card / discovery / health
│   ├── solver/                # OR-Tools Routing 建模 + 距离矩阵
│   ├── verifier/              # 独立校验器
│   ├── eval/                  # runner / graders / report
│   └── db/                    # models / repository
├── web/                       # 静态前端：行程 / Trace / 评测看板
├── data/
│   ├── pois.json
│   ├── matrix.json
│   └── eval_cases.jsonl
├── tests/
└── reports/                   # eval-<sha>.md（CI 产物）
```

### 11.2 环境变量

```
LLM_BASE_URL=            # OpenAI-compatible 端点
LLM_MODEL=
LLM_API_KEY=
ROUTING_PROVIDER=static  # static（默认，读预计算 matrix.json）| osrm | amap
OSRM_BASE_URL=
AMAP_KEY=                # 仅一次性数据导出脚本使用，运行时不需要
DATABASE_URL=sqlite:///./data/tripagent.db
EVAL_CASES_PATH=./data/eval_cases.jsonl
LOG_LEVEL=INFO
MEMORY_INJECTION=on      # 记忆注入开关（on 默认；off 仅评测基线 B 用，ADR-006）
REGISTRY_HEARTBEAT_INTERVAL_S=30  # 能力心跳间隔（§7.4 默认 30s；测试会话停表防竞态）
```

### 11.3 运行

```bash
cp .env.example .env
docker compose up --build
# web  -> http://localhost:3000
# api  -> http://localhost:8000/docs
# 单测与评测
pytest -q
python -m app.eval.runner --baseline
```

### 11.4 重启与版本管理

- `runs` 与 `spans` 持久化，`interrupted` 状态在启动时由恢复任务接管。
- `/healthz` 返回 `version` + `git_sha`，确保线上运行的版本可追溯。
- schema 变更一律走 Alembic 迁移，禁止手工改表。
- 灰度/回滚只改 `capabilities.traffic_weight` 与版本绑定，不重启进程。

---

## 12. 评测指标与验收

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| `hard_constraint_pass_rate` | 硬约束全部满足的用例占比 | **1.00**（40/40，目标 ≥ 0.95 达成） |
| `time_window_violations` | 校验器检出的闭馆/超时冲突总数 | **0**（目标 0 达成） |
| `budget_accuracy` | 重算预算与声明预算一致且不超支的占比 | **1.00** |
| `memory_recall@6` | 第 6 轮仍能正确注入首轮硬约束的占比 | **0.875**（current）vs **0.125**（B 关记忆）vs 不适用（A） |
| `tool_success_rate` | 白名单 function calling + capability 调用的成功数 / 总调用，两类分别统计（§7.6） | **FC 0.958 / capability 1.00** |
| `p95_latency_ms` | 端到端 P95 | **14,445 ms**（真实 LLM 端到端含 chat 多轮；纯 /api/plan 路径 ≈ 2 s） |
| `cost_per_plan` | 单次计划 token 成本 | **≈1,114 token/例**（deepseek-flash） |
| `final_pass_rate` | 所有适用断言同时通过的用例占比 | **0.975**（current 39/40）vs **0.000**（A 纯 LLM）vs **0.800**（B 关记忆） |

> 以上为 2026-11-17 真实评测回填（数据诚实约定）：评测集 v2026-11-17.1（40 条含 8 hidden）、
> git_sha `7731d7c`、模型 deepseek-flash，报告原文 [reports/eval-7731d7c.md](reports/eval-7731d7c.md)。
> A 列全败并非意外：纯 LLM 直出行程在校验器下平均每例 5+ 处时间线/闭馆冲突、不可行请求全部硬凑
> （预算 1 元仍排出付费行程）——即 ADR-006 要复现的失效模式。

**验收规则**：任一硬约束类指标退化即视为回归；`final_pass_rate` 相对于 `baseline` 下降超过 2 个百分点时 CI 失败。所有报告必须注明 `git_sha`、模型名与评测集版本，保证可复现。（已落地：CI `eval` job 对比 `reports/baseline.json`，main 分支保护必需检查 test / lint / eval。）

---

## 13. 实施路线与状态

| 阶段 | 产出 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| **前置 边界确认** | §0 边界确认记录（B1–B13）、ADR-006/007、`TASKS.md` | 13 项实施边界逐条落定并回写文档 | ✅ 完成（2026-11-17） |
| **0 资源就绪** | API key、POI 数据集、Docker Compose、公开 demo、README 架构图 | `docker compose up` 可访问；`/healthz` 返回版本 | 🟡 2026-11-17 基本完成：Windows 本地 compose 验收通过（/healthz 200 含 git_sha、pytest 绿、CI test+lint 绿、pois.json 23 个无缺字段）；VPS 部署验证待做（0.7 尾项） |
| **1 最小可跑** | 单城 3 日行程、Routing 求解、独立校验器、行程前端 | 40 条用例中时间窗冲突为 0 | ✅ 完成（2026-11-17）：`POST /api/plan` 全链路（两阶段 VRPTW 求解 + 独立校验器 + runs/spans 贯穿 + 前端行程卡 + nginx 同源反代）；10 条种子用例（含 2 hidden）时间窗冲突 0 / 预算违规 0 / 必去覆盖全过，属性测试 120 组随机样本通过，pytest 37 绿 + ruff clean + compose 冒烟通过（40 条全集随阶段 3 补齐，见 TASKS.md） |
| **2 记忆层** | Ledger / Views / Policy、多轮一致性演示 | 第 6 轮约束回显正确，可回放 Ledger | ✅ 完成（2026-11-17）：memory_events/memory_views 落地（追加写 + supersedes 归档 + 重放重建）；Memory.read scope 链注入（session→user→global、衰减只影响排序不丢硬约束）；Resolver 白名单 function calling（三工具 + 护栏 + 规则模板降级）；`POST /api/chat` SSE 六段 spans；前端对话视图（用户切换/约束来源徽标）；六轮演进召回 / 跨会话注入 / 用户隔离 / rebuild 一致全过（pytest 106 绿）；任务明细见 `TASKS.md` 阶段 2 |
| **3 评测观测** | 评测集、Trace Store、看板、CI 回归报告 | 产出改动前后指标对比图 | ✅ 完成（2026-11-17）：40 条全集（32 公开 + 8 hidden，chat 多轮内嵌脚本）+ 双基线三列报告（ADR-006，current 0.975 / A 0.000 / B 0.800）+ §12 八指标真实回填 + Trace 瀑布图与评测看板前端视图 + CI `eval` 回归 job（baseline.json 对比，分支保护 test/lint/eval）+「最近一次可行方案」降级收口；报告 [reports/eval-7731d7c.md](reports/eval-7731d7c.md)，任务明细见 `TASKS.md` 阶段 3 |
| **4 平台化** | Registry、健康检查、灰度回滚、故障注入测试、只读 Agent Card 导出 | 回滚无需重启；降级路径可用；卡片与 `/api/plan` schema 一致 | **已完成**（2026-11-18：`capabilities` 表迁移 `c5e01a4b7d02`、5 张默认卡、确定性加权路由、30s 心跳摘除/回切、`/.well-known/agent.json`、pytest 169 绿 + compose 实测灰度→回滚全程不重启） |

> 时间提示：该岗位 2026-09-15 发布、招满即止，10 月起已陆续发放 offer。**优先完成阶段 0–3**，阶段 4 作为加分演示。范围控制遵循 §3.2。
> 2026-11-17 边界确认（B1/B2）：时间盒 ≥ 1 个月，阶段 0–4 全做，首个里程碑为阶段 1；阶段 0–1 任务清单见 `TASKS.md`。

---

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| POI 数据授权与真实性 | 演示可信度 | 高德 Web 服务一次性导出 + 人工校对（B6），README 标注来源与导出日期，运行时不调用外部 API |
| LLM 输出结构不稳定 | 主链路失败 | 结构化输出校验 + 重试 + 规则模板降级；降级率纳入观测 |
| 求解耗时随 POI 数膨胀 | 交互卡顿 | 限制 `n ≤ 30`，设 `time_limit`，矩阵预计算缓存 |
| 范围膨胀导致烂尾 | 无法上线 | 严格遵守非目标；每阶段有可演示产物即冻结范围 |
| **只有设计没有实现** | 面试追问穿帮 | 按 §13 逐阶段推进，所有 `TBD` 由脚本回填，保留 commit 历史与报告原文 |
| SQLite 并发写入 | 评测与在线请求互锁 | WAL + 单写入队列；若并发压测失败再升级 PostgreSQL（触发式，不预先引入） |
| 评测集过拟合 | 指标虚高 | 保留 20% 隐藏用例不参与开发调试，仅在最终验收运行 |

---

## 15. 测试策略

- **单元**：求解建模（时间窗/预算/取舍）、记忆 Policy（冲突与衰减）、校验器（故意构造违反用例）、Registry 发现与权重。
- **契约**：所有 API 用 FastAPI `TestClient` 校验请求/响应 schema；额外断言 `/.well-known/agent.json` 的 `skills[0].id`、输入输出模式与 `/api/plan` 请求 schema 一致，且 `x-tripagent.scope == "discovery-only"`、不存在 A2A task 端点。
- **属性测试**：随机生成 POI 与约束，断言"校验器通过 ⇒ 无硬约束违反"。
- **故障注入**：LLM 超时、矩阵服务不可用、能力健康检查失败，验证降级与 `interrupted` 恢复。
- **回归**：`pytest` + `eval runner` 在 CI 中串行执行，报告入库并对比 baseline。

---

## 16. 面试叙事与证据清单

**开场（30 秒）**："这个岗位五条职责里四条是基础设施，所以我没只做聊天机器人，而是用一个真实业务 Agent 把记忆、评测、注册发现逼出来，并让每层都有可量化证据。"

**技术纵深（2 分钟）**："TripGenie 公开资料提到正在建长期记忆，测评里也提到多轮后预算会丢、行程会出现已关闭景点。我分别用带 provenance 的 Ledger 记忆，和 OR-Tools Routing 加独立校验器处理；场景取舍用 drop penalty 表达，解释为什么不是纯 CP-SAT。"

**收尾（30 秒）**："从原型到上线一个人做完，Docker Compose 可复现，这是改动前后的评测报告 diff。"

**边界追问（15 秒）**："我没上 A2A 协议栈：求解器和校验器是确定性服务，对端不需要自主规划；我只发布一张只读 Agent Card 让发现可被外部读取，卡片里明确写了 discovery-only。真要跨团队委派时的升级条件，我写进了 ADR-009。"

**必须能出示的证据**：可访问 demo URL、`architecture.svg`、评测报告 diff、一条完整 trace 瀑布图、Registry 灰度回滚录屏、commit 历史。

**高频追问准备**：为什么 Routing 不用 `AddCircuit`；为什么记忆不用向量库；评测集如何防止过拟合；Registry 与普通函数调用表的区别；校验器为什么不复用求解器代码；SQLite 在什么条件下必须换掉；function calling 用在哪、为什么不给模型看全部工具；工具参数幻觉、循环调用与版本漂移怎么防；A2A / MCP / function calling 三者区别与为什么不上 A2A；Capability card 与 Agent Card 的区别；什么条件下才会真正引入 A2A；单请求内是 workflow、凭什么仍称 Agent 系统。

---

## 17. 附录：依赖与参考

**核心依赖**：`fastapi`, `uvicorn`, `pydantic`, `ortools`, `sqlalchemy`, `alembic`, `httpx`, `pytest`, `openai`（或任意 OpenAI-compatible SDK）；前端无构建依赖（原生 HTML/CSS/JS + 轻量图表）。

**参考项目与资料**：

- Google OR-Tools — https://github.com/google/or-tools （Routing / VRPTW / CP-SAT）
- TravelPlanner（ICML 2024，多约束行程评测基准）— https://github.com/OSU-NLP-Group/TravelPlanner
- TravelPal（约束求解 + LLM 行程系统，MIT）— https://github.com/xiaojiune/TravelPal
- route-compass（纯前端 TOPTW / ILS 求解器）— https://github.com/DENGYUFAN0/route-compass
- TripChord 目标架构（CP-SAT + RCSP + Pareto 求解器组合）— https://github.com/Oxygen56/TripChord
- PyVRP / VROOM / Timefold Solver（求解器选型对照）
- OpenTripPlanner（多模态路网与耗时矩阵）— https://www.opentripplanner.org
- Google Cloud：TripGenie 长期记忆建设 — https://cloud.google.com/customers/tripgenie
- 携程可观测平台架构实践（观测 engineered at scale 的背景）
- A2A Protocol（Agent2Agent v1.0；Agent Card / Task 状态机）— https://a2a-protocol.org 、https://github.com/a2aproject/A2A
- MCP Specification（Agent ↔ 工具层）— https://modelcontextprotocol.io

> 协议版本与字段以官方 spec 为准；§7.7 的卡片为风格示意，**不声称 A2A 协议完整合规**。
