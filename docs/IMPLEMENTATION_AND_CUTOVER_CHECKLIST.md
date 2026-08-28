# OurA Navi Monitor 实施、删除与切换清单

更新日：2026-08-29

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
- 本次 Git 门已授权：本仓库与 LCS 修复仓库的 commit、push。
- 仍不在本轮授权：构建、部署、Cloud Run、BigQuery/Firestore/Logging 写入/删除、
  IAM、Scheduler、DTS 停用、LCS revision 与任何流量切换。

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
- [x] 晚到事件按本次 event ID/effective partition 去重、补充字段和质量检查。
- [x] `pipeline_run_event_manifest` 与 `pipeline_event_issues` 保存逐事件哈希化去向。
- [x] 质量阻断回滚 facts，但独立保留诊断和 latest failed run；旧成功页面继续可读。
- [x] overview、用户选择和个人页共享鲜度/质量 banner；当天部分日明确标记。
- [x] 3 小时 timing 只有 `app/refresh_policy.py` 一个 owner，Scheduler retry 为 0。
- [x] freeze 等待 Cloud Run execution 与 BigQuery DML；补数绑定固定目标和逐项对账。
- [x] activation、backfill、DTS pause/45 分钟/72 小时验证均产生不可覆盖 receipt。
- [x] Web、Refresh writer、Scheduler invoker 三身份硬分离；镜像必须是同一 digest。
- [x] additive table functions 与 destructive cleanup 分开；旧对象删除 apply 永久硬停止。
- [x] Monitor 与 LCS 均有精确 candidate promotion 门、验收收据、切流前快照和 100% 读回。

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

以下 17 个旧派生对象只做保留盘点，本次不允许删除：

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

`delete_obsolete_monitor_resources.sh` 现在只输出只读清单；任何 `--apply` 都硬停止。
本次只允许在完整依赖门后暂停旧 DTS 自动调度，并保留 transfer config、旧表、raw、
log metrics 和 policy。未来删除必须另做保留期、依赖、回滚和授权设计。

## 7. 从当前状态继续的唯一云端顺序

- [x] 重新只读 inventory 并保存对象类型、生产 revision/image/SHA。
- [x] 运行最终 history plan，人工核对数量并固定只读确认串。
- [ ] 建立 Web runtime、Refresh writer、Scheduler invoker 三个独立身份并完成最小 IAM。
- [ ] 获得 BigQuery/Logging/Firestore 写入授权。
- [x] canonical facts、state、source view 和两个参数化语义函数在线存在。
- [ ] 用最新确认串追平 8/26 的 15/15 差额并逐 event ID 验证。
- [ ] bounded incremental `--until-current`，验证质量门和 dataThrough。
- [ ] 构建 Monitor 无流量候选；IAP 登录并验收三页历史数据、空值和局部失败。
- [ ] 之后才创建/选择 LCS 候选 revision，跑 internal/Web 真实问答和写回成功/失败链。
- [ ] 刷新增窗口，验证同一 canonical 页面同时连续显示历史与新事件。
- [ ] 三次 Scheduler-proven 正式窗口后，以零依赖 receipt 暂停旧 DTS 自动调度。
- [ ] DTS 暂停后完成 45 分钟与 72 小时不变性验证；旧对象继续保留。
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

2026-08-29 最后一次完整本地证据：

- Monitor `pytest`：176 passed；晚到事件、逐事件去向、质量失败、lease、固定目标补数、
  Scheduler provenance、DTS pause/观察和身份绑定均有回归；
- `compileall`：通过；YAML：可解析；
- JavaScript `node --check`、全部 Shell `bash -n`、`git diff --check`：通过；
- Monitor Playwright：30 passed，覆盖三页、局部失败、最新刷新失败仍显示旧成功数据、
  历史未计测环境、停用用户直达链接、
  请求竞态、地图键盘联动、URL 刷新/返回、并发编辑、停用标签、分页状态与
  PC/iPad/mobile；
- BigQuery 只读 dry-run：planned cutover 与所有当前 SQL 通过；projection 11,017 bytes，
  其他当前合同为 0 bytes；无 BigQuery 写入；
- LCS 交叉门禁：后端 992 passed（1 个 httpx deprecation warning），ESLint、TypeScript、
  production build 通过；stale-parent/durability Playwright 7 passed；
- LCS build 有既存的 Browserslist 资料较旧与一个 622 KB chunk warning，不阻断本次
  正确性，但应作为后续性能维护项。

本仓库没有独立的 TypeScript/mypy/ruff/eslint 配置；没有擅自安装第二套工具。
Cloud Build/Docker build 未获本次授权，本轮没有执行。

## 9. 发布状态矩阵

| 层次 | 当前状态 |
| --- | --- |
| 本地代码 | `local validated`：当前两个 repair worktree 已通过本轮完整本地门禁 |
| Git commit | 已获授权；精确 SHA 以仓库历史和本次交付记录为准 |
| Git push | 已获授权；远端状态以 `origin/main` 回读为准 |
| Build | 未执行 |
| IAM | 新的三个独立运行身份尚不存在；当前 live 仍依赖共享 `lcs-agent`，因此 STOP |
| Cloud Run candidate | 本轮未创建 |
| Monitor 登录验收 | 未执行 |
| Monitor 业务验收 | 未执行 |
| BigQuery/Firestore/Logging apply | 本轮未执行；线上已有此前 backfill 与刷新结果 |
| 历史 backfill | 本轮未 apply；两天/当前缺口仍需冻结后用固定目标补齐 |
| Scheduler | live 只读核对仍只有旧 15 分钟 Scheduler ENABLED；新三小时 Scheduler 未创建 |
| LCS revision | 本轮未构建或部署新 revision |
| Monitor/LCS production traffic | 本轮未改变；本次只读回读为 Monitor 00046=100%，LCS 00247=100% |

整体结论：**代码已完成本地闭合；Git 状态以 `origin/main` 回读为准；生产收口尚未完成**。
