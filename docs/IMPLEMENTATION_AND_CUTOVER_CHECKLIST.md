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
- [x] 首页保持七模块；用户详细保持会话双栏；用户管理只在 Monitor 内管理名簿和标签。
- [x] 首页与用户管理长表改成稳定搜索、筛选、排序和分页，状态写入同一 URL owner。
- [x] 桌面首页每页 15 人、管理 20 人；手机分别为 6、8、8 人，避免列表淹没后续模块。
- [x] 方块伪地图删除；真实日本都道府县 SVG 按名单 `エリア` 投影，并单独标记本社・虎ノ門。
- [x] 首页使用依頼任务，个人页明确区分问题主题与依頼任务；未测量历史不伪装成 0。
- [x] 历史合并不再制造 `analytics_tasks=unclassified`；历史任务为空且明确显示履历未计测，
  新 producer 缺少任务则由质量门拒绝。
- [x] 模式和设备排除空值/`unknown`，分别显示无使用、履历未计测、一部计测和已计测。
- [x] 停用用户的分析与会话直达 URL 统一由分析范围 owner 拒绝，并返回用户选择页说明原因。
- [x] 同地区/同角色完整交付覆盖数量使用“名”，回答与事件覆盖数量继续使用“件”。
- [x] 三页统一商务 BI 视觉层级、图表色板、空值/部分计量状态和键盘/ARIA 行为。
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
读取旧 BQ raw payload。旧 BQ 对象尚未删除，因为新旧连续性与线上业务验收未完成。

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

2026-08-26 metadata/read-only：raw request/stdout/stderr 均仍在；旧
`monitor_answer_events=4,176`、`monitor_user_daily=1,184`、snapshot=9 仍未删除。
canonical 已有 `question_events=3,331`、`answer_events=3,215`、
`conversation_events=2,009`、`citation_events=23,281`、`user_scope=83`，两个正式
table function 均存在；`pipeline_state=2`、`pipeline_runs=226`。

这说明过去数据已经回填且没有物理丢失；旧派生对象仍在，但当前代码不再读取它们。
线上旧 Monitor 页面问题不能再归因于“canonical 全空”，必须区分旧 SHA 的前端实现、
最新 Firestore 与 canonical 的 15 条差额，以及尚未切换的 LCS 统一事件 revision。

### 5.2 历史 plan

首次旧实现运行 8 分钟卡在逐会话 `messages` RPC，手动中止，无写入。当前实现于
2026-08-26 只读重跑，58.6 秒完成：

- Firestore：83 roots、2,205 conversations、7,291 messages；
- retired audit：4,176 materialized rows → 3,441 unique request/trace；
- canonical plan：3,346 questions、3,230 answers、1,557 conversations、18,418 citations；
- questions：2,937 Firestore + 409 legacy-only；
- 111 条明确失败保留为失败事实，但不再作为代表性完整交付率分母；
- 37 empty conversations、375 out-of-scope events、312 identities outside roster 被明确排除；
- 6 名 80 人范围用户尚无可绑定聊天历史；
- `issueCount=0`；最终只读确认串：
  `lcs-developer-483404.oura_navi_monitor:2026-03-16:2026-08-26:3346:3230:0`。

线上 `pipeline_state` 仍记录上一次已验证 apply 的 8/25 确认串（3,331/3,215）。新 plan
比线上多 15/15；本轮未 apply。未来 apply 会先重跑，数据变化时拒绝旧确认串。

### 5.3 SQL dry-run

最后一次只读 dry-run：完整计划顺序
`fact/state → source → history → projection → incremental → quality → API`
通过，预计处理字节为 0。9 个单文件也全部通过；其中 projection/history dry-run 约
22–34 KB，incremental/quality 约 1.0–1.3 MB。旧文档记载的三个前置失败已经过时。

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

## 7. 从当前状态继续的唯一云端顺序

- [x] 重新只读 inventory 并保存对象类型、生产 revision/image/SHA。
- [x] 运行最终 history plan，人工核对数量并固定只读确认串。
- [ ] 获得 BigQuery/Logging/Firestore 写入授权。
- [x] canonical facts、state、source view 和两个参数化语义函数在线存在。
- [ ] 用最新确认串追平 8/26 的 15/15 差额并逐 event ID 验证。
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

2026-08-26 最后一次完整本地证据：

- `pytest`：133 passed；历史任务、停用用户、模式/设备计测、单日回访率、活性度边界
  等新增 RED 先失败，修复后定向与全量均通过；
- `compileall`：通过；`pip check`：无破损依赖；YAML：可解析；
- JavaScript `node --check`、全部 Shell `bash -n`、`git diff --check`：通过；
- Playwright：27 passed，覆盖三页、局部失败、历史未计测环境、停用用户直达链接、
  请求竞态、地图键盘联动、URL 刷新/返回、并发编辑、停用标签、分页状态与
  PC/iPad/mobile；
- Chromium 快照：1440px 三页及 390px 首页/用户/管理已人工检查；页面无全局横向溢出，
  产品矩阵仅在自身卡片内横向滚动；
- BigQuery 只读 dry-run：计划序列与 9 个单文件全部通过；
- 真实只读 Chromium：三页实际 BigQuery/Firestore 链路 0 个模块错误，管理范围
  83/69/80/3；这不替代 IAP。

本仓库没有独立的 TypeScript/mypy/ruff/eslint 配置；没有擅自安装第二套工具。
Cloud Build/Docker build 属于用户明确禁止的未授权构建动作，本轮没有执行。

## 9. 发布状态矩阵

| 层次 | 当前状态 |
| --- | --- |
| 本地代码 | 本地最终 diff 已通过完整门禁与真实历史只读产品链路 |
| Git commit | 以当前仓库 `HEAD` 为准；只证明源码版本，不证明构建或运行状态 |
| Git push | 以 `origin/main` 为准；只证明远端源码版本，不证明部署状态 |
| Build | 未执行 |
| Cloud Run candidate | 本轮未创建；现有 `oura-navi-monitor-00042-jum` Ready，镜像是旧提交 `66da5e4`，candidate tag 与 100% 流量同指该 revision |
| Monitor 登录验收 | 未执行 |
| Monitor 业务验收 | 未执行 |
| BigQuery/Firestore/Logging apply | 本轮未执行；线上已有此前 backfill 与刷新结果 |
| 历史 backfill | 8/25 已验证 3,331/3,215；8/26 只读 plan 多出 15/15，未 apply |
| LCS revision | 生产 `00243-sas` 为 100%；`00247-jug` 仅 candidate tag、0% 生产流量；本轮未改 |
| Monitor/LCS production traffic | 本轮未改变；Monitor 00042=100%，LCS 00243=100% |

整体结论：**尚未完成**。
