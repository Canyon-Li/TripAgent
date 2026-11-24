# 分支推进与文档过期审计 · 2026-11-23（第三轮，周一）

- 扫描对象：工作区 HEAD `f88fa85`（分支 fix/cost-transparency，与上轮相同）；main `d3ea906` = 本地 origin/main（同步，截至 11-20 成功 fetch）。
- 工作区：不干净——tracked 改动 0；未跟踪新增一批课程材料（见 A-1）。
- 验证结果：pytest **4 failed / 182 passed**（周一特有，见 P1）；ruff 全仓通过；mypy 无错（46 文件）；check_plans **32/32**（源码自 1562dce 无变更）。
- fetch：**失败**（`Connection closed by UNKNOWN port 65535`，SSH 链路掐断——AGENTS.md 已记此类问题）。按规则退回本地引用比较；本地 main 与 origin/main 引用一致（11-20 时点已同步，此后远程状态未知）。
- 增量基线：2026-11-20 报告。成本短路不适用（工作区不干净、上轮非全绿）。
- 2026-11-21/22 无审计报告落盘（当日未产出）。

## 一句话摘要

抓到一个真 P1：**chat 测试种子不带 start_date、锚定「今天」，故宫周一闭馆——每周一 pytest 必红**（本轮 4 failed/182 passed，同 HEAD 上周五全绿）；上轮 P2 ×3 中 2 项已在 main 闭环（工作区分支落后 4 提交看不到）、1 项延续；项目 3 天无提交，剩余工作全在阶段 5 上线动作。

## A. 分支推进

| 项 | 状态 |
| --- | --- |
| 工作区（HEAD） | fix/cost-transparency @ `f88fa85`（11-19），与上轮完全相同；tracked 文件零改动 |
| 未跟踪新增 | **课程材料入库根目录**：MISSION.md、NOTES.md、RESOURCES.md、assets/（2 文件）、lessons/（1）、learning-records/（1）、reference/（1）、.scratch/（6）——内容为「上线工程」学习课程（授课/测验/词汇表）与部署脚本。全部未跟踪；.scratch/ 在 main 已 ignore（e60a7fa），其余未 ignore |
| main vs origin/main | 0/0（本地引用一致；fetch 失败，远程最新状态未验证） |
| main...HEAD | HEAD 领先 1（5.2 费用透明修复）、落后 4——**连续第三轮停在同一状态，合并确认（AGENTS.md 流程）仍未完成** |
| 分支清单 | main（d3ea906，11-19）；fix/cost-transparency（f88fa85，11-19）；docs/stage5-tasks 已合并未删（清洁度：可删，延续提示）。无 >14 天未动分支，无 stale 引用 |
| 任务进度（main 口径） | 47[x]/4[ ]：阶段 0–4 全满；阶段 5 **0/4**（5.1 投递 MJ036607 未完成、5.2 dogfood、5.3 外部用户、5.4 美化） |
| 活跃度 | main 与分支均 **3 天无提交**（11-19 后）；结合 NOTES.md 自述「系统学习上线工程」模式，代码侧暂停与上线准备并行，属正常节奏；但合并确认是仓内动作，勿遗漏 |

业务含义：功能与文档已全部收口，剩余 4 项全是上线动作；仓内唯一的悬挂动作是 fix/cost-transparency 的合并确认。

## B. 文档过期

文档清单（git 跟踪 *.md，13 个）：README.md、TASKS.md、project.md、AGENTS.md、reports/audit-{2026-11-18}.md、reports/audit-latest.md、reports/eval-*.md（7 个）。注意：**reports/audit-2026-11-20.md 未入库**（上轮产出于分支工作区、未提交），tracked 的 audit-latest.md 内容仍是 11-18 版（f15e543 入库），工作区版为 11-20——上轮报告在 main 上不可见。未跟踪的非生成文档另有课程材料一批（见 A-1）。

### P1 事实不符（1 条，新增）

**P1-1 测试套件日期依赖：周一必红**（首次发现 2026-11-23）

- 声称：README.md:114「pytest …186 项」+ 分支保护「test 必过」（ci.yml）+ TASKS 各验收记录「pytest …绿」
- 实际：2026-11-23（周一）pytest **4 failed / 182 passed**；同 HEAD（f88fa85）11-20（周五）实测 186 passed
- 失败族：chat 种子类测试（.pytest_cache lastfailed 5 条：test_chat_fallback 3 条、test_api_chat::test_unknown_poi_name_mapped_and_unknown_dropped、test_resolver::test_recall_constraint_recorded；缓存 5 条与全量 4 failed 的差异未逐条溯源，失败族一致）
- 根因链（证据）：
  1. `app/api/chat.py:336-337`：请求无 start_date 时 `dt.date.today()` 锚定当天
  2. `data/pois.json`：forbidden_city（故宫博物院）`closed_weekdays=[0]`（周一闭馆）
  3. `tests/test_chat_fallback.py:32-47` 等种子 `_post(...)` **不带 start_date**，种子消息「预算9000玩1天必去故宫」→ 第 1 天=今天=周一 → must_visit 撞闭馆日 → infeasible ≠ 预期 feasible
  4. 对照组：`data/eval_cases.jsonl` 40 条用例**全部带固定 start_date**，故 check_plans 任何一天都 32/32——chat 测试缺同样的固定
- 定性：产品行为正确（拒绝周一故宫行程是对的）；是**测试的隐藏时间炸弹**——每逢周一本地必红；CI 仅 push/PR 触发无 schedule，所以主分支一直没暴露
- 建议（三选一，①最小）：① 测试种子统一带固定 start_date（选未来的开放日，如周六）；② conftest 冻结 `date.today`（monkeypatch）；③ CI 加 schedule 触发，防此类时间依赖复发

### P2 结构漂移（1 条延续，2 条确认闭环）

- **延续 P2-3 .gitignore:35 `docs/` 整目录忽略、约定未注明**（自 2026-11-18 起，第三轮；HEAD 与 main 两侧该行均未变）
- 闭环：P2-1 /api/pois 已入 §10 契约（main:project.md:447，含「阶段 1 另加」注记）
- 闭环：P2-2 分支保护清单已补 typecheck（main:project.md:585 四项）

### P3 时间久远（1 条新增）

**P3-1 main 侧 project.md 头部「最后更新」滞后一天**

- 头部（main）：最后更新 2026-11-18；实际 main 上最后提交 e60a7fa（2026-11-19，勘误含 project.md）
- 建议：下次回写时改 2026-11-19（工作区分支 checkout 上的头部为 11-18 且其文件最后提交也是 11-18，自洽；此条只针对 main 侧）

### 已核对一致的声明（本轮实测）

| 声称 | 出处 | 实际 |
| --- | --- | --- |
| pytest 186 项（数量） | README.md:114 | 收集 186，数量一致（「绿」的声称受 P1-1 影响，见上） |
| check_plans 32/32 | TASKS 3.10 | 实测 32/32 |
| POI 23 / 矩阵 24×24 / 评测集 40 | TASKS 0.4/0.5、project.md:595 | 实测一致 |
| /api/pois 已入 §10 契约 | main:project.md:447 | 确认（上轮 P2-1 闭环证据） |
| 分支保护四项 | project.md:585（main） | 确认（上轮 P2-2 闭环证据） |
| §12 指标 0.975/0.000/0.800 引用链 | project.md:50/576 | 引用与报告数值一致（未复跑真实 LLM 评测，历史声称不变） |

## 与上份报告（2026-11-20）的增量对比

| 上轮条目 | 本轮状态 |
| --- | --- |
| P1（无） | **新增 P1-1**：chat 测试日期依赖，周一 4 failed |
| P2-1 /api/pois 契约缺漏 | **闭环**（main:project.md:447；工作区分支 checkout 仍是旧版，随合并自然同步） |
| P2-2 分支保护清单缺 typecheck | **闭环**（main:project.md:585） |
| P2-3 docs/ ignore 约定未注明 | **延续**（自 2026-11-18 起，第三轮） |
| P3（无新增） | **新增 P3-1**：main 侧 project.md 头部日期滞后一天 |

## 建议回写（只建议，未改原文）

1. 测试修复（P1-1）：chat 测试种子统一带固定 start_date，或 conftest 冻结 today；建议顺带 CI 加 schedule
2. project.md（main）头部：最后更新 → 2026-11-19（P3-1）
3. .gitignore:35：加注释注明 docs/ 为本地材料目录，或收窄规则（P2-3，第三轮延续）
4. 流程：确认合并 fix/cost-transparency（1 提交，第三轮提醒）；删除已合并的 docs/stage5-tasks
5. 仓库卫生：课程材料（MISSION/NOTES/RESOURCES/assets/lessons/learning-records/reference）移出仓库根或加入 .gitignore——当前未跟踪未 ignore，公开仓有误提交风险
6. 上轮报告可见性：reports/audit-2026-11-20.md 尚未提交（仅存在于分支工作区），建议与 audit 本轮报告一并入库，保持审计链在 main 可见

## 执行记录

- 白名单命令：pytest（4 failed/182 passed，256s）、ruff check .、mypy app、check_plans.py（32/32）
- 本轮执行注意：pytest 与 check_plans 曾并行执行，曾怀疑进程间干扰；后经单测复跑与根因定位排除（失败为日期依赖，确定性复现），干扰假设不成立
- fetch 失败（SSH 连接关闭），已按规则退回本地引用比较并在报告开头注明
- 写操作仅限本报告与 audit-latest.md；其余只有验证命令的缓存目录
