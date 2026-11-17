# 评测报告 · bcb01d3

- 生成时间：2026-11-17 17:58 UTC
- git_sha：`bcb01d3`；模型：`scripted-fake`（脚本替身/无 LLM）
- 评测集：v2026-11-17.1（40 条 = 公开 32 + 隐藏 8），本次运行范围：40 条全集（含 hidden）
- 评测集内容摘要（sha256 前 12 位）：f64861ffbbb8
- 列：current = 现行管线；A = 纯 LLM 直接生成（ADR-006，仅 plan 用例）；B = 完整管线关闭记忆注入（ADR-006）

## 指标（§12）

| 指标 | current | A | B |
| --- | --- | --- | --- |
| 硬约束通过率 | 1.000 | — | — |
| 时间窗冲突总数 | 0.000 | — | — |
| 预算准确率 | 1.000 | — | — |
| memory_recall（chat 用例） | 1.000 | — | — |
| 工具成功率·function calling | 0.938 | — | — |
| 工具成功率·capability | 1.000 | — | — |
| P95 延迟 ms（记录型） | 6,113 | — | — |
| 单均 token 成本（记录型） | 72.000 | — | — |
| final_pass_rate | 1.000 | — | — |

## 逐用例（final 口径：所有适用断言同时通过）

### current

| case | hidden | 结果 | 失败原因 |
| --- | --- | --- | --- |
| seed_basic_3day |  | ✅ |  |
| seed_1day_nearby_free |  | ✅ |  |
| seed_tight_budget |  | ✅ |  |
| seed_greatwall_far |  | ✅ |  |
| seed_tight_daily_cap |  | ✅ |  |
| seed_infeasible_budget |  | ✅ |  |
| seed_infeasible_closed_monday |  | ✅ |  |
| seed_prefs_only |  | ✅ |  |
| seed_hidden_4day_must_spread | Y | ✅ |  |
| seed_hidden_infeasible_budget_1 | Y | ✅ |  |
| solo_budget_only |  | ✅ |  |
| solo_prefs_nature |  | ✅ |  |
| solo_must_capital_museum |  | ✅ |  |
| solo_cap_360 |  | ✅ |  |
| multi_history_cluster |  | ✅ |  |
| multi_family_zoo_olympic |  | ✅ |  |
| multi_food_streets |  | ✅ |  |
| multi_5day_classics |  | ✅ |  |
| multi_narrow_window_pair |  | ✅ |  |
| multi_modern_art |  | ✅ |  |
| parks_nature_3day |  | ✅ |  |
| conflict_cap_vs_duration |  | ✅ |  |
| conflict_cap_vs_three_big |  | ✅ |  |
| conflict_budget_vs_tickets |  | ✅ |  |
| conflict_monday_pair |  | ✅ |  |
| conflict_two_walls_one_day |  | ✅ |  |
| infeasible_cap60_square |  | ✅ |  |
| infeasible_zero_budget_paid |  | ✅ |  |
| hidden_narrow_window_museum | Y | ✅ |  |
| hidden_monday_2day_slip | Y | ✅ |  |
| hidden_far_spread_2day | Y | ✅ |  |
| hidden_infeasible_ticket_sum | Y | ✅ |  |
| chat_two_turn_evolution |  | ✅ |  |
| chat_recall_by_reference |  | ✅ |  |
| chat_six_turn_reference |  | ✅ |  |
| chat_clarify_then_complete |  | ✅ |  |
| chat_user_scope_cross_session |  | ✅ |  |
| chat_infeasible_then_adjust |  | ✅ |  |
| chat_hidden_recall_4turn | Y | ✅ |  |
| chat_hidden_budget_supersede_recall | Y | ✅ |  |

**final 通过数**：current 40/40
