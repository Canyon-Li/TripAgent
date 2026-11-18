# 评测报告 · 53cc2f8

- 生成时间：2026-11-18 04:40 UTC
- git_sha：`53cc2f8`；模型：`scripted-fake`（脚本替身/无 LLM）
- 评测集：v2026-11-17.1（40 条 = 公开 32 + 隐藏 8），本次运行范围：公开用例（hidden 不参与，§14）
- 评测集内容摘要（sha256 前 12 位）：f64861ffbbb8
- 列：current = 现行管线；A = 纯 LLM 直接生成（ADR-006，仅 plan 用例）；B = 完整管线关闭记忆注入（ADR-006）

## 指标（§12）

| 指标 | current | A | B |
| --- | --- | --- | --- |
| 硬约束通过率 | 1.000 | — | 0.844 |
| 时间窗冲突总数 | 0.000 | — | 0.000 |
| 预算准确率 | 1.000 | — | 1.000 |
| memory_recall（chat 用例） | 1.000 | — | 0.167 |
| 工具成功率·function calling | 0.917 | — | 0.917 |
| 工具成功率·capability | 1.000 | — | 1.000 |
| P95 延迟 ms（记录型） | 7,371 | — | 4,273 |
| 单均 token 成本（记录型） | 63.750 | — | 63.750 |
| final_pass_rate | 1.000 | — | 0.844 |

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
| chat_two_turn_evolution |  | ✅ |  |
| chat_recall_by_reference |  | ✅ |  |
| chat_six_turn_reference |  | ✅ |  |
| chat_clarify_then_complete |  | ✅ |  |
| chat_user_scope_cross_session |  | ✅ |  |
| chat_infeasible_then_adjust |  | ✅ |  |

### B

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
| chat_two_turn_evolution |  | ❌ | 轮次类型 ['plan', 'clarification'] != 期望 ['plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |
| chat_recall_by_reference |  | ❌ | 轮次类型 ['plan', 'clarification'] != 期望 ['plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |
| chat_six_turn_reference |  | ❌ | 轮次类型 ['plan', 'clarification', 'plan', 'clarification', 'clarification', 'clarification'] != 期望 ['plan', 'plan', 'plan', 'plan', 'plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget；memory_recall: 末轮缺字段 preferences；memory_recall: 末轮缺字段 daily_minutes_cap；memory_recall: 末轮缺字段 must_visit |
| chat_clarify_then_complete |  | ✅ |  |
| chat_user_scope_cross_session |  | ❌ | 轮次类型 ['plan', 'clarification'] != 期望 ['plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |
| chat_infeasible_then_adjust |  | ❌ | memory_recall: 末轮缺字段 must_visit；必去点未覆盖: ['summer_palace'] |

**final 通过数**：current 32/32，B 27/32
