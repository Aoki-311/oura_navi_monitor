# OurA Navi Monitor 实施、删除与切换清单

更新日：2026-08-30

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
- [x] 活性度、7 日消息数与唯一 answer join 统一；Summary 人群由当前有效名簿中
  `本社MR` / `コントラクトMR` 角色动态计算，用户分析使用独立的 USER_MAP 范围，
  不再把历史 69/80 人数当成运行合同。
- [x] 前端导航、preset、导出统一 AbortController，旧响应不能覆盖新页面。
- [x] 用户编辑、标签编辑/删除携带 expected revision，repository transaction 再检查。
- [x] 停用标签保留显示但不可新分配；冲突时抽屉不关闭。
- [x] 用户分析与会话双栏独立读取，未知 role 和坏 messageCount 局部排除。
- [x] 地图、Chart.js 空值、表格 fallback、键盘、ARIA、PC/iPad/mobile 合同。
- [x] 静态 HTML/JS/CSS 与全部 API `no-store`，前端 `fetch` 同时绕过 HTTP 缓存，避免
  revision/schema 恢复后继续显示旧空响应。
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
- [x] 历史 Excel 已只读核对 83=61+8+11+3、旧 global=69、旧 user/map=80；这些数字只用于
  迁移对账，不是当前 Summary 的验收值；源文件中没有地点字典。
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
- [ ] 用最终提交 SHA 重新执行真实云端只读 inventory；已有结果仅是旧基线快照，本轮没有云写。
- [x] 最后一次业务代码修改后的 Python 全量回归通过并记录新证据。
- [x] 最后一次业务代码修改后的 JS syntax、脚本/YAML/SQL 合同和 E2E 通过并记录新证据。
- [x] 最终 diff、旧引用、敏感信息、用户文件和 release state 已在提交前复核；云端
  release state 仍按独立门处理。
- [x] 晚到事件按本次 event ID/effective partition 去重、补充字段和质量检查。
- [x] `pipeline_run_event_manifest` 与 `pipeline_event_issues` 保存逐事件哈希化去向。
- [x] 质量阻断回滚 facts，但独立保留诊断和 latest failed run；旧成功页面继续可读。
- [x] overview、用户选择和个人页共享鲜度/质量 banner；当天部分日明确标记。
- [x] 3 小时 timing 只有 `app/refresh_policy.py` 一个 owner，Scheduler retry 为 0。
- [x] freeze 等待 Cloud Run execution 与 BigQuery DML；补数绑定固定目标和逐项对账。
- [x] Job deploy、activation、backfill、DTS pause preflight/apply、45 分钟/72 小时验证均
  产生受控 receipt；首次 DTS apply 在 60 分钟内消费 preflight、先落 intent 且重跑全部
  live 门，disabled 后中断可由同一 intent 幂等补齐 final，backfill 与
  正式 execution 均绑定同一 digest/identity。
- [x] Monitor candidate 保持当前 runtime identity；Refresh writer 与 Scheduler invoker
  在数据切换时分离；Monitor 与 Job 镜像必须是同一 digest。
- [x] additive v2 table functions 与旧对象 retirement 分开；验证 published run 后才精确执行 retirement。
- [x] Monitor promotion 强制消费同一 activation → DTS pause → 45 分钟 → 72 小时收据链，
  并在切流前再次要求精确 candidate=0%，现场复核 Job/Scheduler/DTS 未漂移；LCS 仍使用
  其独立 promotion 门。
- [x] Monitor promotion 的同一 intent `pre/post` 也不能并发接管；锁内全部现场重读完成后，
  紧贴切流前重新验证 intent 绑定的 API/浏览器收据原始 bytes 与 60 分钟时限，失败保留锁。
  `pre/post` 崩溃后的清锁是单独授权动作，只有不可变 `final` 可自动 exact recovery。
- [x] `.gcloudignore` 明确上传 Cloud Build 所需 config/dev requirements/deploy/scripts/tests/e2e，
  同时排除 credentials、node_modules 和测试产物；`.dockerignore` 继续只允许 runtime 文件。

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

## 5. 历史只读环境快照（不是当前发布证明）

### 5.1 2026-08-26 至 2026-08-29 的 BigQuery 快照

2026-08-26 metadata/read-only：raw request/stdout/stderr 均仍在；旧
`monitor_answer_events=4,176`、`monitor_user_daily=1,184`、snapshot=9 仍未删除。
canonical 已有 `question_events=3,331`、`answer_events=3,215`、
`conversation_events=2,009`、`citation_events=23,281`、`user_scope=83`，两个正式
table function 均存在；`pipeline_state=2`、`pipeline_runs=226`。

这说明过去数据已经回填且没有物理丢失；旧派生对象仍在，但当前代码不再读取它们。
线上旧 Monitor 页面问题不能再归因于“canonical 全空”，必须区分旧 SHA 的前端实现、
最新 Firestore 与 canonical 的 15 条差额，以及尚未切换的 LCS 统一事件 revision。

2026-08-29 additive 修复后的当前读回为 `question_events=3,334`、
`answer_events=3,218`；新增三张诊断/manifest 表、lease/运行字段、两张 source view 和
两个正式函数均通过只读合同验证，并在 64 MiB 费用硬上限内各返回一条真实样本。published
水位仍停在 8/27 00:57:05 UTC，所以这是
“旧历史重新可读”，不是“两天补数完成”。

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

- [ ] 使用最终提交 SHA 重新只读 inventory 并保存对象类型、生产 revision/image/SHA。
- [ ] 冻结当前 Monitor runtime 与 GitHub trigger build identity；push 自动生成待审批 build，
  不以新建 IAM 或修改 trigger 为前置条件。
- [ ] 激活数据链前确认不同的 Refresh writer 与 Scheduler invoker；如需新建或改权，另行
  完成最小 IAM 授权。
- [ ] 获得 BigQuery/Logging/Firestore 写入授权。
- [ ] 用本轮固定凭据重新确认 canonical facts、additive state/诊断表、source view 和两个
  参数化语义函数在线存在并生成当前只读 schema 收据；不得运行 destructive retirement。
- [ ] 构建 Monitor 无流量候选；确认 candidate tag 精确指向本 SHA/revision/URL、当前 runtime
  identity 未被部署参数暗改；IAP 登录并验收三页历史数据、空值和局部失败，但保持 0%。
- [ ] 构建 LCS 无流量候选；六条业务问答路由逐条验证 `monitor.v2`、revision+trace+span、
  一请求一 question、服务器持久化与失败不抹正文；四条 debug 路由验证不进入业务指标。
- [ ] 先把精确 LCS candidate revision/image/SHA/build/runtime identity 注册进 Monitor v2
  revision registry，保存不可覆盖 registration receipt；此时不得提前开启严格 enforcement。
- [ ] 独立授权 LCS 流量并生成原子 promotion final；等待该收据中的 `drainUntil`，确认旧正流量
  revision 的在途请求已排空。中断时只允许以同一 intent/参数恢复，禁止创建第二条切流链。
- [ ] `drainUntil` 后用同一 promotion final 激活严格 v2 enforcement；BigQuery ledger 是 durable
  authority，若数据库已提交而本地收据中断，只从精确 ledger 恢复，不重做或覆盖激活。
- [ ] 使用当前源数据重新运行 history plan，人工核对数量并固定只读确认串；再用最新确认串
  追平 8/26 的 15/15 差额并逐 event ID 验证。
- [ ] 用精确确认串部署与 Monitor candidate 相同 digest 的 Job，保存不可覆盖 deploy receipt，
  并保持新旧 Scheduler 都 PAUSED。
- [ ] bounded incremental `--until-current`，从本次 execution 回读 digest/identity/终态，验证
  duplicate durable disposition、质量门、dataThrough，以及严格 current v2 中
  `http_trace_contract_unavailable=0`。
- [ ] 启用新 Scheduler；三次 Scheduler-proven 正式窗口的每个 execution 都验证相同
  digest/identity/terminal success，AttemptFinished 必须成功。
- [ ] 以零依赖 receipt 完成全只读 DTS pause preflight；60 分钟内重跑全部门、消费
  preflight receipt 并先写受控 intent 后才暂停旧 DTS 自动调度；若 disabled 后中断，使用
  同一 intent/参数只读恢复 final，禁止新建第二条暂停链或自动回滚。
- [ ] DTS 暂停后完成 45 分钟与 72 小时不变性验证；旧对象继续保留。
- [ ] 72 小时通过后重新验收 Monitor 0% candidate，绑定完整 receipt 链后才切 Monitor
  Web 流量；不得用已经完成的 LCS 流量门替代 Monitor 登录/业务验收。

任一步失败：停止后续 release 动作，修复唯一 owner；不恢复旧表读取或建立 fallback。

## 8. 最终验证命令（最后修改后填写）

```bash
PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/python -m compileall -q app scripts tests
find frontend e2e/tests -name '*.js' -not -path '*/node_modules/*' -print0 \
  | xargs -0 -n1 node --check
for script_file in scripts/*.sh; do bash -n "$script_file"; done
./scripts/run_e2e.sh
.venv/bin/python scripts/dry_run_monitor_sql.py \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
git diff --check
```

2026-08-30 最终源树本地证据：

- Monitor `pytest`：360 passed（124.99 秒）；覆盖晚到事件、逐事件去向、质量失败不覆盖
  已发布正文、lease、固定目标补数、完整计量轴、角色范围、CSV、registration、promotion、
  enforcement、Scheduler provenance、DTS pause/恢复、45 分钟/72 小时观察与身份绑定；
- Monitor `compileall`、Cloud Build YAML 解析、JavaScript `node --check`、全部 Shell
  `bash -n`、`git diff --check`：通过；
- Monitor Playwright：96 passed（单 worker，0 failed）；覆盖三页完整事务、缓存禁用、
  Summary 精确角色、用户分析较宽范围、用户管理 scope/label 关系、CSV 创建到下载事务、
  局部失败不抹正文、旧响应竞态、历史未计测、PC/iPad/mobile 与恢复后的旧兼容响应；
- LCS 后端：1081 passed、1 个既有 httpx deprecation warning；promotion 聚焦 28 passed；
  compile、两个 Cloud Build YAML、全部受影响 shell、ESLint、production build 与
  `npm audit --audit-level=high` 通过，0 vulnerabilities；
- LCS 单 worker 全量 Playwright：175 passed、2 个按 WebKit 能力设计跳过、0 failed；其中
  浏览器恢复不能伪造服务端持久化、局部失败不抹回答正文和旧历史兼容路径均有回归；
- 真实 Cloud Build 上传清单：Monitor 155 文件 / 1,783,540 bytes，LCS 598 文件 /
  41,916,499 bytes；新增 runtime、发布脚本和测试均包含，credentials、node_modules、
  release/test 产物均未包含；
- 最终 SQL 结构与语义由本地合同覆盖，但本轮未用真实 BigQuery 重新 dry-run；Docker daemon
  也未执行本地镜像构建。这两项不能被上述本地通过替代，必须在真实 Build/只读云端门取证；
- LCS build 仍有既有 Browserslist 资料较旧与一个 622 KB chunk warning，不阻断正确性，
  但应作为后续性能维护项。

本仓库没有独立的 TypeScript/mypy/ruff/eslint 配置；没有擅自安装第二套工具。
本地结果不代替 Cloud Build。main push 后必须读取自动触发 build 的原始步骤；build、
0% candidate、登录浏览器、业务验收、traffic、Scheduler 三周期和 DTS 退役仍分别取证。

## 9. 发布状态矩阵

| 层次 | 当前状态 |
| --- | --- |
| 本地代码 | 同一最终源树的完整 Python、浏览器、语法、YAML、shell、上传边界和敏感信息门均已通过 |
| Git commit | 本文不写自引用 SHA；交付时以 `git rev-parse HEAD` 的外部回读为准 |
| Git push | 交付时必须以远端 `refs/heads/main` 与本地 HEAD 精确相等的外部回读为准，不能从本地 commit 推断 |
| Build | 历史基线 Build `5e23172c-1eb1-4e24-958a-e7ede4a91e11` 只证明 `4fefea4`；本次 source commit 的终态 Build 必须另行读取 |
| IAM | source build 不需要新 IAM；Job/Scheduler 身份与权限仍是后续激活门，未执行 |
| Cloud Run candidate | 历史基线 revision `oura-navi-monitor-4fefea4` 曾 Ready 且 100%；本次 source commit 的 0% candidate 尚无当前证据 |
| Monitor 登录验收 | 未执行 |
| Monitor 业务验收 | 未执行 |
| BigQuery/Firestore/Logging apply | 本轮未执行；线上已有此前 backfill 与刷新结果 |
| 历史 backfill | 基线 manual backfill execution 已成功到 `2026-08-29T14:30:49Z`；不能作为下一 digest 的 receipt |
| Scheduler | 旧 quarter-hour、新 three-hour Scheduler 与 DTS 状态仅有历史快照；本次 source commit 尚未完成三窗口与 DTS pause 真实取证 |
| LCS revision | 本轮未构建或部署新 revision |
| Monitor/LCS production traffic | 本轮未改变；Monitor `oura-navi-monitor-4fefea4`=100%，下一版 traffic 未授权 |

整体结论：**源代码与本地发布合同已完成最终验证；Git 运输必须由外部 SHA 回读证明；
Build、candidate、数据、登录浏览器、业务、Scheduler/DTS 与 traffic 仍是独立生产门，尚未收口**。
