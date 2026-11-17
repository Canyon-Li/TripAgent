# 评测报告 · 7731d7c

- 生成时间：2026-11-17 16:34 UTC
- git_sha：`7731d7c`；模型：`deepseek-flash`（真实 LLM）
- 评测集：v2026-11-17.1（40 条 = 公开 32 + 隐藏 8），本次运行范围：40 条全集（含 hidden）
- 评测集内容摘要（sha256 前 12 位）：-
- 列：current = 现行管线；A = 纯 LLM 直接生成（ADR-006，仅 plan 用例）；B = 完整管线关闭记忆注入（ADR-006）

## 指标（§12）

| 指标 | current | A | B |
| --- | --- | --- | --- |
| 硬约束通过率 | 1.000 | 0.000 | 1.000 |
| 时间窗冲突总数 | 0.000 | 168.000 | 0.000 |
| 预算准确率 | 1.000 | 0.812 | 1.000 |
| memory_recall（chat 用例） | 0.875 | — | 0.125 |
| 工具成功率·function calling | 0.958 | — | 0.979 |
| 工具成功率·capability | 1.000 | — | 1.000 |
| P95 延迟 ms（记录型） | 14,445 | 0.000 | 12,960 |
| 单均 token 成本（记录型） | 1,114 | 0.000 | 1,099 |
| final_pass_rate | 0.975 | 0.000 | 0.800 |

> 注：A 列 P95/成本 0.000 = 本次运行未计量（baseline_a 计量在本次运行后补齐，后续运行生效）；
> A 列仅 plan 用例（32 条），chat 类指标不适用。current 唯一失败用例 chat_six_turn_reference：
> 真实 LLM 第 6 轮从注入的对话历史复读出 budget=6500（值正确，source=request 而非记忆通道），
> 且第 2 轮未提交 preferences 更新——暴露「模型可绕过记忆通道从 history 复读」的真实行为，如实计入。

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
| chat_six_turn_reference |  | ❌ | memory_recall[budget]: 来源 request 不含前缀 memory_views#；memory_recall[budget]: 轮次 6 != 3；memory_recall[preferences]: 值 ['history'] != 期望 ['nature', 'history']；memory_recall[preferences]: 轮次 1 != 2 |
| chat_clarify_then_complete |  | ✅ |  |
| chat_user_scope_cross_session |  | ✅ |  |
| chat_infeasible_then_adjust |  | ✅ |  |
| chat_hidden_recall_4turn | Y | ✅ |  |
| chat_hidden_budget_supersede_recall | Y | ✅ |  |

### A

| case | hidden | 结果 | 失败原因 |
| --- | --- | --- | --- |
| seed_basic_3day |  | ❌ | 校验器检出 9 处冲突（期望 0） |
| seed_1day_nearby_free |  | ❌ | 校验器检出 2 处冲突（期望 0） |
| seed_tight_budget |  | ❌ | 校验器检出 5 处冲突（期望 0） |
| seed_greatwall_far |  | ❌ | 校验器检出 5 处冲突（期望 0） |
| seed_tight_daily_cap |  | ❌ | 校验器检出 2 处冲突（期望 0） |
| seed_infeasible_budget |  | ❌ | status=feasible，期望 infeasible；预算超支: 重算 91.3 > 20.0；校验器检出 2 处冲突（期望 0） |
| seed_infeasible_closed_monday |  | ❌ | status=feasible，期望 infeasible；校验器检出 2 处冲突（期望 0） |
| seed_prefs_only |  | ❌ | 校验器检出 8 处冲突（期望 0） |
| seed_hidden_4day_must_spread | Y | ❌ | 校验器检出 10 处冲突（期望 0） |
| seed_hidden_infeasible_budget_1 | Y | ❌ | status=feasible，期望 infeasible；预算超支: 重算 124.3 > 1.0；校验器检出 7 处冲突（期望 0） |
| solo_budget_only |  | ❌ | 校验器检出 8 处冲突（期望 0） |
| solo_prefs_nature |  | ❌ | 校验器检出 3 处冲突（期望 0） |
| solo_must_capital_museum |  | ❌ | 校验器检出 1 处冲突（期望 0） |
| solo_cap_360 |  | ❌ | 校验器检出 6 处冲突（期望 0） |
| multi_history_cluster |  | ❌ | 校验器检出 7 处冲突（期望 0） |
| multi_family_zoo_olympic |  | ❌ | 校验器检出 7 处冲突（期望 0） |
| multi_food_streets |  | ❌ | 校验器检出 8 处冲突（期望 0） |
| multi_5day_classics |  | ❌ | 校验器检出 12 处冲突（期望 0） |
| multi_narrow_window_pair |  | ❌ | 校验器检出 2 处冲突（期望 0） |
| multi_modern_art |  | ❌ | 校验器检出 4 处冲突（期望 0） |
| parks_nature_3day |  | ❌ | 校验器检出 9 处冲突（期望 0） |
| conflict_cap_vs_duration |  | ❌ | status=feasible，期望 infeasible；校验器检出 2 处冲突（期望 0） |
| conflict_cap_vs_three_big |  | ❌ | status=feasible，期望 infeasible；校验器检出 6 处冲突（期望 0） |
| conflict_budget_vs_tickets |  | ❌ | status=feasible，期望 infeasible；预算超支: 重算 583.2 > 50.0；校验器检出 5 处冲突（期望 0） |
| conflict_monday_pair |  | ❌ | baseline_A 无可解析行程: ["day 字段非法: {'date_index': 0, 'stops': []}"] |
| conflict_two_walls_one_day |  | ❌ | status=feasible，期望 infeasible；校验器检出 4 处冲突（期望 0） |
| infeasible_cap60_square |  | ❌ | status=feasible，期望 infeasible；校验器检出 2 处冲突（期望 0） |
| infeasible_zero_budget_paid |  | ❌ | status=feasible，期望 infeasible；预算超支: 重算 128.1 > 1.0；校验器检出 7 处冲突（期望 0） |
| hidden_narrow_window_museum | Y | ❌ | 校验器检出 1 处冲突（期望 0） |
| hidden_monday_2day_slip | Y | ❌ | 校验器检出 8 处冲突（期望 0） |
| hidden_far_spread_2day | Y | ❌ | status=feasible，期望 infeasible；校验器检出 6 处冲突（期望 0） |
| hidden_infeasible_ticket_sum | Y | ❌ | status=feasible，期望 infeasible；预算超支: 重算 918.3 > 120.0；校验器检出 7 处冲突（期望 0） |

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
| chat_two_turn_evolution |  | ❌ | 轮次类型 ['clarification', 'plan'] != 期望 ['plan', 'plan']；memory_recall[budget]: 来源 request 不含前缀 memory_views#；memory_recall[budget]: 轮次 4 != 1 |
| chat_recall_by_reference |  | ❌ | 轮次类型 ['clarification', 'clarification'] != 期望 ['plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |
| chat_six_turn_reference |  | ❌ | 轮次类型 ['clarification', 'plan', 'plan', 'plan', 'clarification', 'clarification'] != 期望 ['plan', 'plan', 'plan', 'plan', 'plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget；memory_recall: 末轮缺字段 preferences；memory_recall: 末轮缺字段 daily_minutes_cap；memory_recall: 末轮缺字段 must_visit |
| chat_clarify_then_complete |  | ❌ | 轮次类型 ['plan', 'plan'] != 期望 ['clarification', 'plan'] |
| chat_user_scope_cross_session |  | ❌ | 轮次类型 ['plan', 'clarification'] != 期望 ['plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |
| chat_infeasible_then_adjust |  | ❌ | memory_recall[budget]: 轮次 4 != 2；memory_recall[must_visit]: 来源 request 不含前缀 memory_views#；memory_recall[must_visit]: 轮次 4 != 1 |
| chat_hidden_recall_4turn | Y | ❌ | 轮次类型 ['plan', 'plan', 'clarification', 'clarification'] != 期望 ['plan', 'plan', 'plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget；memory_recall: 末轮缺字段 daily_minutes_cap；memory_recall: 末轮缺字段 must_visit |
| chat_hidden_budget_supersede_recall | Y | ❌ | 轮次类型 ['plan', 'plan', 'clarification'] != 期望 ['plan', 'plan', 'plan']；末轮终态 clarification != 期望 feasible；memory_recall: 末轮缺字段 budget |

**final 通过数**：current 39/40，A 0/32，B 32/40
