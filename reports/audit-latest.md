# 分支推进与文档过期审计 · 2026-11-18（首轮）

- 扫描对象：git HEAD `0c20079`（开始）→ `53cc2f8`（结束）。扫描期间 PR #2（AGENTS.md 入库）并发合入并已推送。
- 工作区：全程干净，无未提交文件。
- 验证结果：pytest **133 passed**（125.02s）；check_plans **32/32**；ruff 在 CI 口径内通过（全仓 16 错，见 P2-1）。
- 首轮基线：无上份报告可比。成本短路条件不适用（首轮全量执行）。

## 一句话摘要

分支推进健康：阶段 3 已冻结合入、37/38 勾选、唯一开放项 0.7 VPS 验证；文档过期 P1 ×1（README 测试数 128 → 实际 133）、P2 ×3、P3 ×2。

## A. 分支推进

| 项 | 状态 |
| --- | --- |
| 工作区 | 干净，无未提交/未跟踪文件 |
| main vs origin/main | 同步（0 ahead / 0 behind，`53cc2f8`） |
| 分支 | 仅 main。feat/phase3-eval-observation 已合并并删除（PR #1）；`git remote prune --dry-run` 为空，无 stale 引用 |
| worktree | 仅主工作区 |
| 任务进度 | 阶段 0：8/9；阶段 1：9/9；阶段 2：10/10；阶段 3：10/10；合计 **37/38** |
| 未勾项 | **0.7 VPS 部署验证**（Windows 本地已过，VPS 待部署时执行）——与 project.md:589 🟡 一致 |
| 尾项核对 | 阶段 2 尾项「最近一次可行方案」已由 3.4 收口 ✅（TASKS.md:68）；无新增悬置尾项 |

下一步（由文档推导）：阶段 4 平台化（README:106；TASKS 尚无阶段 4 清单 → P3-2）。

## B. 文档过期

文档清单（git ls-files "*.md"，共 8 个）：README.md、TASKS.md、project.md、reports/eval-{0bb2802,376cbde,3d6a5d8,7731d7c,bcb01d3}.md。另：docs/report-01/02 被 .gitignore 忽略仅存本地（P2-3）；AGENTS.md 扫描期间入库（PR #2）。

### P1 事实不符（1 条）

**P1-1 README 测试数过期：128 → 实际 133**（首次发现 2026-11-18）

- 声称：README.md:93「pytest（契约 / 求解 / … / 评测等 128 项）」
- 实际：pytest 133 passed
- 来源链：7731d7c 更新 README 时为 128；1562dce 加 5 个测试至 133——该 commit 的 TASKS 验收记录写「pytest 133 绿」（正确），同 commit 改了 README 其他段落但漏改此数
- 建议：README.md:93「128 项」→「133 项」

### P2 结构漂移（3 条）

**P2-1 全仓 ruff 16 错，全部在 alembic/versions/ 生成迁移文件**

- 证据：`ruff check .` → 16 errors（W291 尾随空格、UP035 `Sequence` 导入等），全部位于 alembic/versions/12cd442bb18f_create_runs_and_spans.py
- 口径说明：CI lint 范围是 `ruff check app tests scripts alembic/env.py`（ci.yml:22），不含 alembic/versions/——文档「ruff clean」在其自定口径内成立，不算虚假声明
- 风险：任何人跑全仓 lint 都会看到 16 错；pyproject.toml 未排除该目录
- 建议：pyproject `[tool.ruff]` 加 `extend-exclude = ["alembic/versions"]`，或修掉这 16 处

**P2-2 环境变量契约缺 MEMORY_INJECTION**

- 三方对齐：.env.example 10 键、代码全部读取（app/ 内每个键 ≥1 文件引用）、文档提及 9/10
- 缺口：`MEMORY_INJECTION`（基线 B「关记忆注入」开关，ADR-006）在 .env.example 与 app/ 3 个文件中存在，project.md §11.2 环境变量契约（:532–538）与 README 均未提及；仅 TASKS.md:67（3.3 记录）提及
- 建议：project.md §11.2 环境变量表补一行，注明「仅评测基线 B 用，运行时不设」

**P2-3 docs/ 整目录被 gitignore，两份技术报告仅本地存在**

- 证据：.gitignore:35 `docs/`；docs/report-01-time-dimension-modeling.md 与 docs/report-02-two-phase-must-visit.md 未入库，且无任何文档引用（无断链）
- 风险：公共仓库读者（project.md 自述目标读者含面试考官）看不到这两份报告；且日后往 docs/ 放任何正式文档都会静默不入库
- 建议：若「仅本地」是有意为之，在 project.md 注明；否则入库两份报告并收窄 ignore 规则

### P3 时间久远（2 条）

**P3-1 project.md 头部「最后更新」滞后**

- 头部：版本 v0.3.0 / 最后更新 2026-11-17（project.md:8–9）
- 实际：文件最后提交 2026-11-18（1562dce，改 §9 runs.response_json、§10 chat start_date）
- 建议：「最后更新」→ 2026-11-18；版本号是否升 v0.3.1 由作者定

**P3-2 阶段 4 清单未拆**

- TASKS.md 头部约定「阶段 3–4 清单在阶段 2 冻结后按同样格式拆出」；阶段 3 已冻结（3.10 验收 ✅）
- README:106 已把「阶段 4 平台化（Registry 灰度回滚 + Agent Card）」标为后续，TASKS.md 无阶段 4 小节
- 建议：按阶段 3 格式拆 4.1–4.x（B16：仅 discovery-only Agent Card）

### 已核对一致的声明（本轮实测，防止下轮误报）

| 声称 | 出处 | 实际 |
| --- | --- | --- |
| 40 条评测集（32 公开 + 8 hidden） | project.md:592、README:91/106、TASKS 3.2 | data/eval_cases.jsonl 40 个 case_id（首行 `_meta` 计数 40/32/8 一致） |
| POI 23 个 | TASKS 0.4 等 | pois.json `pois` 数组 = 23 |
| 矩阵 24×24 | TASKS 0.5 | matrix.json minutes 24×24，24 列全 amap、0 兜底 |
| final_pass_rate 0.975（39/40）/ A 0.000 / B 0.800 | project.md:50/573，引 reports/eval-7731d7c.md | 该报告 :21/:157 数值一致 |
| fake 模式 40/40 | TASKS 3.10 | reports/eval-bcb01d3.md（scripted-fake，current 1.000，A/B 未运行）——与真实 LLM 口径（7731d7c）并行，非矛盾 |
| check_plans 32/32 | TASKS 3.10 | 实测 32/32 passed |
| 文档互链 4 个目标 | README/project.md | project.md / TASKS.md / .env.example / reports/eval-7731d7c.md 全部存在 |
| README 引用的脚本与入口 | README | export_pois / build_matrix / check_plans / badcase_to_eval / app/main.py / web/index.html / architecture.svg 全部存在 |
| 代码路由 ⊆ §10 契约 | app/ grep | 代码 11 条路由（/healthz /readyz /api/plan /api/chat /api/pois /api/runs/{trace_id} /api/memory/events|views|rebuild /api/eval/run|reports）均在契约内；契约多出的 /api/registry/* 2 端点属阶段 4 规划（README:106 明示后续），不算漂移 |
| TBD 残留 | project.md | 仅 3 处且均为约定声明（:14、:608）或真实待办（:589 0.7 VPS）；§12 八指标无未回填 TBD |

## 建议回写（只建议，未改原文）

1. README.md:93：「128 项」→「133 项」（P1-1）
2. project.md:9：「最后更新」→ 2026-11-18（P3-1）
3. pyproject.toml `[tool.ruff]`：补 extend-exclude 或修 16 处（P2-1）
4. project.md §11.2：环境变量表补 MEMORY_INJECTION（P2-2）
5. docs/report-01/02：入库或注明仅本地（P2-3）
6. TASKS.md：按阶段 3 格式拆阶段 4 清单（P3-2）

## 执行记录

- 白名单命令：pytest（133 passed，125s）、ruff check（静态）、check_plans.py（读源码确认仅写系统临时目录后运行）
- 禁入白名单未执行：alembic、docker、export/build 数据脚本
- `GIT_TERMINAL_PROMPT=0 git fetch origin` 两次均成功；无网络失败
- 写操作仅限本报告与 audit-latest.md；其余只有验证命令的缓存目录（.pytest_cache / __pycache__）
