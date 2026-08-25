# OurA Navi Monitor 实施、删除与切换清单

更新日：2026-08-26

规则：只有代码处于最终状态、冲突旧路径关闭且最后一次修改后的对应验证通过，
本地项目才能打勾。云端写入、构建、部署、登录、业务和流量是独立授权与证据。

## 1. 最终目标、根因与范围

- 最终目标：三页在历史、新、空、部分缺失、单模块失败、慢请求、并发编辑和移动端
  场景下均可用；过去能证明的数据尽量显示，不能证明的字段显示未测量。
- 根因：新 UI 读取空 canonical facts；旧历史仍在 retired table/Firestore/raw logs；
  页面 mega-contract、全局鲜度门、null→0 和未取消请求放大为空白页。
- 唯一 owner：历史编译、canonical facts、两个参数化语义函数、AnalyticsService、
  page controller、用户管理 transaction、独立 conversation repository。
- 影响范围：本仓库后端、前端、SQL、脚本、测试和权威文档。
- 不在本轮授权：commit、push、构建、部署、Cloud Run、BigQuery/Firestore/Logging
  写入/删除、LCS revision 与任何流量切换。

## 2. 本地必选任务

- [x] 页面级请求和模块状态替代全局页面 gate。
- [x] overview、regions、users、user detail、conversations 独立合同和失败边界。
- [x] 缺失、未测量、真实 0 和零事件日期轴的语义分开。
- [x] 单日回访率返回 `null`；P95 和完整交付返回 measured/total 覆盖数量。
- [x] 活性度、7 日消息数、唯一 answer join 与 69/80 动态范围统一。
- [x] 前端导航、preset、导出统一 AbortController，旧响应不能覆盖新页面。
- [x] 用户编辑、标签编辑/删除携带 expected revision，repository transaction 再检查。
- [x] 停用标签保留显示但不可新分配；冲突时抽屉不关闭。
- [x] 用户分析与会话双栏独立读取，未知 role 和坏 messageCount 局部排除。
- [x] 地图、Chart.js 空值、表格 fallback、键盘、ARIA、PC/iPad/mobile 合同。
- [x] 静态 HTML/JS/CSS `no-store`，避免 revision 资源混用。
- [x] `user_daily`/snapshot/overview mega view/detail mega view owner 从代码关闭。
- [x] 增量刷新最多 24 小时一批，并由同一 owner 追到冻结当前水位。
- [x] retained raw sink 范围包含 request、canonical stdout、旧 terminal/request marker
  与 stderr runtime truth，不接收全部 stdout 噪声。
- [x] 全量历史读取从逐会话 N+1 改为三条集合流，并有 120 秒 RPC deadline/进度。
- [x] 无有效时间的会话明确排除，不再用处理时刻伪造使用日期。
- [x] 重复邮箱 root、绑定邮箱冲突、subject 冲突均 fail closed，不任意选人。
- [x] 旧表按 request/trace 去重，只迁移可证明字段；旧 success flag、正文、raw payload、
  邮箱不迁移。
- [x] 旧问题类型只做封闭一次性枚举转换；旧默认 `topic_ideation` 进入 unclassified。
- [x] 历史 apply 前检查编译 event ID 重复，apply 后按全部 expected event ID 验证。
- [x] 真实 Excel 只读核对 83=61+8+11+3、global=69、user/map=80；无地点字典。
- [x] 真实云端只读 inventory 和历史 plan；没有云写。
- [x] 最后一次业务代码修改后的 Python 全量回归通过。
- [x] 最后一次业务代码修改后的 JS syntax、脚本/YAML/SQL 合同和 E2E 通过。
- [x] 最终 diff、旧引用、敏感信息、用户文件和 release state 复核。

## 3. 已关闭的本地旧路径

已删除的旧运行 owner：

```text
app/routers/history.py
app/routers/metrics.py
app/services/bigquery_metrics.py
app/services/firestore_history.py
app/services/google_auth.py
frontend/adapters/dashboardAdapter.js
frontend/viewModels/metricStatus.js
sql/create_views.sql
sql/create_aggregate_tables.sql
sql/refresh_daily.sql
scripts/setup_aggregate_refresh.sh
scripts/refresh_aggregate_tables.sh
```

`app/routers/trace.py` 被保留并改写，因为用户明确要求会话列表 + 消息列表；它不再
读取旧 BQ raw payload。旧 BQ 对象尚未删除，因为历史 apply/验收未执行。

## 4. 用户已有文件保护

以下五个开始时即为未跟踪文件，本轮不得修改、stage 或上传：

```text
docs/AURA_NAVI_MONITOR_USER_DATA_FIELD_CATALOG_2026-08-23.xlsx
docs/FIELD_ANALYSIS_AND_BI_PLAN_2026-08-23.md
docs/FIELD_DICTIONARY_2026-08-23.xlsx
docs/MONITOR_CAPABILITY_GAP_AUDIT_2026-08-22.md
monitor_field_split_8_1.xlsx
```

`OurA-Navi_userlist.xlsx` 仅只读核对，没有保存或改写。

## 5. 只读真实环境证据

### 5.1 当前 BigQuery

2026-08-25 metadata/read-only：raw request/stdout/stderr 均仍在；
`monitor_answer_events=4,176`；新 `question_events=0`、`answer_events=0`、
`pipeline_state=0`、`pipeline_runs=0`；旧 `user_daily` 与旧 dashboard view 仍在。

这说明过去数据没有物理删除。空页面的直接原因是新页面已读取 canonical chain，
但 canonical facts 尚未发布和回填。

### 5.2 历史 plan

首次旧实现运行 8 分钟卡在逐会话 `messages` RPC，手动中止，无写入。最终实现于
2026-08-26 只读重跑，62.1 秒完成：

- Firestore：83 roots、2,195 conversations、7,265 messages；
- retired audit：4,176 materialized rows → 3,441 unique request/trace；
- canonical plan：3,331 questions、3,215 answers、1,546 conversations、18,357 citations；
- questions：2,923 Firestore + 408 legacy-only；
- complete-delivery measured：111 / 3,215；
- 37 empty conversations、375 out-of-scope events、312 identities outside roster 被明确排除；
- 6 名 80 人范围用户尚无可绑定聊天历史；
- `issueCount=0`；最终只读确认串：
  `lcs-developer-483404.oura_navi_monitor:2026-03-16:2026-08-25:3331:3215:0`。

该确认串只证明本次 plan 内容，尚未写入 `pipeline_state`，不能解锁旧对象删除。
未来 apply 会先重跑同一 plan；只要数据变化，脚本就拒绝旧确认串并要求重新人工核对。

### 5.3 SQL dry-run

最后一次只读 dry-run：完整计划顺序
`fact/state → source → history → projection → incremental → quality → API`
通过，预计处理字节为 0。单独运行时，dataset、source/fact/state/API DDL 与
Firestore projection 通过。

当前线上前置状态导致的预期失败：

- `merge_incremental.sql`：`http_request_source` 尚未发布；
- `merge_history.sql`：线上事实表尚无新 lineage 字段；
- `check_data_quality.sql`：source view 尚未发布。

这些不是允许忽略的“测试通过”；必须按第 7 节顺序创建 owner 后重新 dry-run/apply。

## 6. 未来精确删除清单

raw 三表永久保留，不删行：

```text
run_googleapis_com_requests
run_googleapis_com_stdout
run_googleapis_com_stderr
```

历史 apply、event ID 验证、页面验收全部通过后，才允许删除 17 个旧派生对象：

```text
monitor_answer_events
monitor_user_daily
monitor_system_hourly
monitor_dashboard_snapshots
v_monitor_excluded_identities
v_requests
v_query_suggest_results
v_query_suggest_degraded
v_sync_telemetry
v_ask_audit_events
v_followup_resolution_events
v_followup_open_result_events
v_coverage_gap_workitems
v_request_user_metric_events
v_answer_action_events
v_monitor_event_message_join_keys
run_googleapis_com_varlog_system
```

另行只删除已 inventory 的旧 DTS、四个 obsolete log metrics 和只依赖旧合同的精确
policy ID。脚本无 glob、无 dataset delete、无 raw delete；`--apply` 需另行授权、
精确确认字符串、完整 DTS resource name、批准凭据和 `issueCount=0` 的
`--history-confirm`。该确认串必须已由成功的逐 event ID 验证写入
`pipeline_state(source=history_rebuild)`；只跑 plan 不能解锁删除。

## 7. 唯一云端执行顺序（均未授权/未执行）

- [ ] 重新只读 inventory 并保存对象类型、DTS ID、生产 revision/image/SHA。
- [x] 运行最终 history plan，人工核对数量并固定只读确认串。
- [ ] 获得 BigQuery/Logging/Firestore 写入授权。
- [ ] 原地创建/扩充 canonical facts、state 和两个参数化语义函数。
- [ ] 发布 `monitor_event_source`、`http_request_source`；不切页面到第二套 API。
- [ ] history apply，逐 event ID 验证 questions/answers/conversations/citations 全部落表。
- [ ] bounded incremental `--until-current`，验证质量门和 dataThrough。
- [ ] 构建 Monitor 无流量候选；IAP 登录并验收三页历史数据、空值和局部失败。
- [ ] 之后才创建/选择 LCS 候选 revision，跑 internal/Web 真实问答和写回成功/失败链。
- [ ] 刷新增窗口，验证同一 canonical 页面同时连续显示历史与新事件。
- [ ] 停旧 DTS，精确删除旧派生 owner；再次验证页面。
- [ ] 分别完成 Monitor/LCS 业务验收和明确流量切换。

任一步失败：停止后续 release 动作，修复唯一 owner；不恢复旧表读取或建立 fallback。

## 8. 最终验证命令（最后修改后填写）

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/python -m compileall -q app scripts tests
find frontend e2e/tests -name '*.js' -not -path '*/node_modules/*' -print0 \
  | xargs -0 -n1 node --check
for script_file in scripts/*.sh; do bash -n "$script_file"; done
./scripts/run_e2e.sh
.venv/bin/python scripts/dry_run_monitor_sql.py
git diff --check
```

2026-08-26 最终本地证据：

- `pytest`：106 passed；
- `compileall`：通过；`pip check`：无破损依赖；YAML：可解析；
- JavaScript `node --check`、全部 Shell `bash -n`、`git diff --check`：通过；
- Playwright：17 passed，覆盖三页、局部失败、未知历史分类、请求竞态、地图联动、
  并发编辑、停用标签与 PC/iPad/mobile；
- BigQuery 只读 dry-run：完整计划顺序通过；线上未发布前的三个单文件前置失败保留为
  明确证据，不被伪装成通过。

本仓库没有独立的 TypeScript/mypy/ruff/eslint 配置；没有擅自安装第二套工具。
Cloud Build/Docker build 属于用户明确禁止的未授权构建动作，本轮没有执行。

## 9. 发布状态矩阵

| 层次 | 当前状态 |
| --- | --- |
| 本地代码 | 最终业务代码已完成并通过本地全量门禁；尚未发布到云端 |
| Git commit | 未执行；无本轮 commit |
| Git push | 未执行；origin/main 未改变 |
| Build | 未执行 |
| Cloud Run candidate | 本轮未创建、未部署；当前线上候选状态未重新核验 |
| Monitor 登录验收 | 未执行 |
| Monitor 业务验收 | 未执行 |
| BigQuery/Firestore/Logging apply | 未执行；只读检查而已 |
| 历史 backfill | 未执行；只有 plan |
| LCS revision | 本轮未构建、未部署、未切换；既有候选不算本轮验收 |
| Monitor/LCS production traffic | 本轮未改变；当前实际分流需在发布阶段重新只读核验 |

整体结论：**尚未完成**。
