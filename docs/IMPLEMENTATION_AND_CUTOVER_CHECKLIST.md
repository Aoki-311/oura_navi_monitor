# OurA Navi Monitor 实施、删除与切换清单

更新日：2026-08-24

本清单记录“代码是否实现”和“现实环境是否已经验证”两个不同层次。只有必选项在
最终状态完成、旧路径关闭并通过对应验证后才能打勾。

## 1. 本地实施清单

- [x] 明确最终目标、系统根因、唯一 owner、影响范围、删除范围和完成标准。
- [x] LCS RequestSpec 增加封闭问题类型、任务和产品字段，无第二次分类调用。
- [x] LCS 四种结构化事件由 active 路径直接输出。
- [x] LCS accepted question 在预检之后记录，拒绝请求不制造孤儿问题。
- [x] 社内/Web 正常与错误路径都有统一 answer terminal owner。
- [x] Web 逐需求交付由同一次 Writer call 机器注释提供，缺失不猜成功。
- [x] assistant 写回和 feedback/action 由 message route 记录。
- [x] HMAC 用户键和事件 payload allowlist 阻止邮箱/正文进入日志。
- [x] 产品自由文本不能直接进入事件；只有现有受管产品身份解析成功的标准名/key
  可以发布，并保留候选/解析数量说明图表覆盖度。
- [x] BigQuery 使用同名 dataset、正式事实、日聚合、水位和三个 API view。
- [x] 增量刷新替换 15 分钟全量重建；事实、聚合、质量门和水位原子发布，所有
  查询有时间边界和扫描上限。
- [x] 完整交付公式、失败优先级和测量缺失规则只有一套。
- [x] 69/80 只由部门和有效状态决定；83 只是管理名单数量，不是权限 scope。
- [x] 真实 Excel 只读计划得到 69/80/83，虎ノ門和首都圏A分开。
- [x] 地区身份直接使用名单 `エリア`；只把 `本社 + 虎ノ門` 记为
  `本社・虎ノ門`，无第二张地区代码表。
- [x] 名单、标签、审计原子写入；标签不改变分析范围和 IAP。
- [x] Firestore 身份、会话、引用投影没有静默 500 行截断。
- [x] 后端只暴露正式 analytics/admin/trace/export API。
- [x] 三个页面、首页七模块、日本 SVG、地区联动、个人双栏会话、标签管理完成。
- [x] PC/iPad/mobile 响应式合同加入 E2E。
- [x] 旧代码、SQL、脚本、页面、adapter 和冲突文档从工作树删除。
- [x] Monitor Cloud Build 只创建无流量候选；正式切流只有一个显式脚本。
- [x] 生产身份使用 IAP 注入的 `x-goog-authenticated-user-email` 并强制三名管理员
  allowlist；本地 header 只能在显式本机测试模式使用。
- [x] 后端正式响应的内层字段也是封闭 Pydantic 契约；前端缺字段直接显示
  模块错误，不再把缺失活性度、计数或比较对象悄悄补成 0/休眠/空对象。
- [x] 最后一次相关修改后的全部 RED、回归、合同、编译、脚本、YAML、E2E 重跑。
- [x] 最终 diff、敏感信息、旧引用、用户已有文件和发布边界复核。

本地实施清单已关闭；云端迁移、真实数据、IAP 和业务验收仍未获授权执行，因此
整体结论仍是“尚未完成”。

## 2. 本地已删除的旧路径

### Monitor 后端/前端

```text
app/routers/history.py
app/routers/metrics.py
app/services/bigquery_metrics.py
app/services/firestore_history.py
app/services/google_auth.py
app/static/ops.html
frontend/adapters/dashboardAdapter.js
frontend/viewModels/metricStatus.js
e2e/tests/chart-stability.spec.js
e2e/tests/dashboard-partial-failure.spec.js
tests/test_security_and_ui_guardrails.py
app/domain/complete_delivery.py
tests/test_complete_delivery.py
```

旧 `trace.py` 没有删除，因为用户明确要求保留会话列表 + 消息列表。它已重写为
只按 `roster_id` 验证 80 人范围并分页读取 Firestore 消息，不再读取旧 BQ payload。

### SQL/运维/部署

```text
sql/create_views.sql
sql/create_aggregate_tables.sql
scripts/setup_aggregate_refresh.sh
scripts/refresh_aggregate_tables.sh
scripts/create_sa_key.sh
scripts/deploy_cloud_run.sh
scripts/deploy_cloud_run_yaml.sh
scripts/run_e2e_chart_stability.sh
deploy/cloudrun.service.yaml
```

`cloudbuild.yaml` 是 Monitor Web 服务候选构建的唯一部署 owner；
`scripts/promote_candidate.sh` 是正式流量的唯一 owner。

### 冲突文档

```text
docs/MONITOR_API_CONTRACT.md
docs/MONITOR_DATA_ARCHITECTURE.md
docs/MONITOR_FRONTEND_IMPLEMENTATION_PLAN.md
docs/MONITOR_FRONTEND_INFORMATION_ARCHITECTURE.md
docs/MONITOR_IMPLEMENTATION_GAP_ANALYSIS.md
docs/MONITOR_METRIC_CONTRACT.md
docs/MONITOR_UPGRADE_EXECUTION_PLAN.md
```

LCS 删除：

```text
backend/docs/OBSERVABILITY_LOG_SCHEMA_FREEZE.md
```

替换为 `backend/docs/MONITOR_ANALYTICS_EVENT_CONTRACT.md`。

## 3. 明确保留且未修改/未删除的用户调查产物

以下未跟踪文件属于用户/Claude 调查产物，本次只读或完全未碰：

```text
docs/AURA_NAVI_MONITOR_USER_DATA_FIELD_CATALOG_2026-08-23.xlsx
docs/FIELD_ANALYSIS_AND_BI_PLAN_2026-08-23.md
docs/FIELD_DICTIONARY_2026-08-23.xlsx
docs/MONITOR_CAPABILITY_GAP_AUDIT_2026-08-22.md
monitor_field_split_8_1.xlsx
```

它们不是运行 owner，也不应自动上传；其中 XLSX/原始盘点可能包含姓名、邮箱、
问题或回答示例，分享前必须另行做 PII 检查。

## 4. 未来云端精确删除清单

本轮没有执行以下删除。执行前必须只读 inventory，确认每个对象真实存在、类型
正确、没有未知下游消费者，并由用户另外授权。

### BigQuery 对象（18 个）

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
run_googleapis_com_stderr
run_googleapis_com_varlog_system
```

保留 dataset `oura_navi_monitor` 和两个同名 raw owner：
`run_googleapis_com_requests`、`run_googleapis_com_stdout`。sink 收窄后，新 LCS 第一条
结构化事件会让保留的 stdout 表取得正式 `jsonPayload` schema；不得删除/重建 raw
表。原地切换时只在这两个表内精确删除 `ANALYTICS_START_AT` 之前的旧行。

### DTS

删除旧 aggregate scheduled query 的精确 transfer config。执行脚本要求完整资源名：

```text
projects/{project}/locations/{location}/transferConfigs/{id}
```

### Logging metrics

```text
lcs_rag_app_qs_total
lcs_rag_app_qs_degraded
lcs_rag_app_restore_total
lcs_rag_app_restore_failed
```

### Monitoring policies

只删除只依赖 Query Suggest 或旧 restore 合同的精确 policy ID，并用可重复的
`--policy-id projects/{project}/alertPolicies/{id}` 传入。通知渠道保留并复用于新的
HTTP 5xx、回答失败、事件发射失败、刷新失败和刷新过期告警。

删除脚本默认只打印 18 个完整 BQ 对象、保留的两个 raw 表和待删 policy ID；
`--apply` 还要求精确确认字符串、DTS 完整资源名和冻结时间；没有 glob 或 dataset
级删除。

## 5. 云端实施前 STOP 条件

任一条件不满足就停止，不得删除旧对象或切流：

- 没有用户明确授权云端写入/删除；
- 未确认实际 `lcs-rag-app` revision、image digest、Git SHA 和部署源；
- 未确认 Cloud Logging 实际保留边界和 exclusions；
- 未确认 Firestore 最早可读时间和必要索引；
- 未冻结 `MONITOR_ANALYTICS_START_AT`；
- 未验收 83/69/80 名单结果；
- 未用真实登录样本证明 LCS 分析事件 `user_id`、已验证 Firestore 根文档
  `subject` 与 Monitor 私有名单绑定的是同一员工；
- 结构化 stdout 尚未证明落为 `jsonPayload`；
- Web demand 注释真实遵循率、request ID 连接或 PII 检查失败；
- Monitor 候选未完成 IAP 登录和业务口径验收。

## 6. 一次性原地切换顺序

不建立 shadow dataset、backup table、旧 API fallback 或长期双读。

1. 获得云端只读核查授权，形成精确 inventory。
2. 用户确认 `ANALYTICS_START_AT` 以及此前数据永久舍弃。
3. 获得写入授权后，导入名单并启用两个 Firestore TTL collection group。
4. 停止旧 DTS；保持 Monitor 维护状态。
5. 运行 `bootstrap_gcp.sh --stage prepare`：创建正式事实/聚合表并把同名 Logging
   sink 收窄到 request log + 结构化事件；此阶段不创建 Job/Scheduler。
6. 部署 LCS 无正式流量候选，跑 internal/Web 和保存成功/失败真实链路。
7. 确认保留的同名 raw stdout 已有正式 `jsonPayload`，再发布两个正式 source view。
8. 从仍可取得的 Logging 和 Firestore 执行一次性增量重建；质量门成功并写入水位。
9. 检查去重、连接、枚举、PII、69/80/83、完整交付和日期边界。
10. 精确删除第 4 节旧对象、DTS、metrics 和 policies。
11. 运行 `bootstrap_gcp.sh --stage activate --analytics-start-at ...`；脚本必须验证
    source view 和一次成功重建水位后，才启动唯一 refresh job + 15 分钟 scheduler。
12. 构建 Monitor 无流量候选；完成 IAP 登录、三页和真实数据业务验收。
13. 显式把已验收 Monitor revision 切到 100%。
14. 确认生产流量、事件和数据水位，再结束维护。

## 7. 本地验证记录

最终验证必须从最后一次相关修改之后开始。目标命令如下，结果在执行结束后填写：

### Monitor Python

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q app scripts tests
```

状态：通过。唯一 `user_id` 身份合同调整后的最终 Monitor 全量回归
`72 passed`；身份、Firestore 投影、用户目录、名单和 SQL focused 回归
`43 passed`，compileall 无错误。

### LCS 合同与回归

```bash
PYTHONPATH=backend/src python3 -m pytest -q backend/tests
```

状态：通过。最终完整门禁 `963 passed`；唯一身份/事件/候选发布 focused 回归
`143 passed`。另有 1 条既有 `httpx` raw content
弃用警告，不影响测试结果。

### 脚本、YAML、SQL 合同

```bash
find frontend e2e -name '*.js' -not -path '*/node_modules/*' -print0 | xargs -0 -n1 node --check
for script_file in scripts/*.sh; do bash -n "$script_file"; done
.venv/bin/python -m pip check
git diff --check
```

另用 PyYAML 解析 Monitor/LCS 两套 Cloud Build 与环境 YAML；用正式 renderer
展开 9 份 SQL 和原子 publisher，确认没有 `${...}` 遗留。所有 cloud 脚本只运行
不带 `--apply` 的 plan 分支。

状态：全部通过。LCS frontend ESLint、production build、npm audit 通过，
pip-audit 为 0 findings；真实名单 plan 为 83 人，`global=69`、`user_map=80`、
`management=83`，部门 61/11/8/3；删除 plan 精确打印 18 个旧 BQ 对象。

### 前端本地真实页面链路

```bash
./scripts/run_e2e.sh
```

该测试使用真实 FastAPI、真实静态页面、真实浏览器渲染和 mocked 正式 API
响应；它能证明路由、页面、图表、地图、表格、管理交互和响应式布局，但不能证明
真实 BigQuery/Firestore 数据或 IAP。

状态：`10 passed`。本机 Docker daemon 未运行，因此两套容器镜像的本地 Docker
build 未执行；Cloud Build、候选 revision、登录验收、真实业务数据和生产流量均未
在本轮改变或验收。

状态：通过。`10 passed (20.0s)`；覆盖三页、七模块、日文地区键地图联动、
未知分类拒绝、缺失活性度不伪装、标签管理、会话双栏以及 PC/iPad/mobile。

### 最终差异与安全复核

- 最终 commit/push 后必须分别回读 Monitor/LCS 的 `HEAD`、`origin/main` 和
  `git ls-remote origin refs/heads/main`；本清单不把旧 SHA 当作新提交的证明；
- 两仓 `git diff --check` 通过，旧 owner 文件均已在本地工作树消失；
- active `backend/src`、`backend/deploy` 与 Monitor 运行目录没有旧日志事件读取/输出；
- 私钥、API key、OAuth token 模式扫描无命中；正式配置中只保留用户明确指定的
  3 个 IAP 管理员邮箱，其他邮箱均为 `example.com` 测试数据；
- 五份用户/Claude 调查产物仍为未跟踪文件，mtime 保持原值，未被运行链消费；
- 测试端口 8099 已关闭，没有残留本地服务。

## 8. 发布状态矩阵

| 层次 | 当前状态 | 证明 |
| --- | --- | --- |
| 本地代码 | Monitor 身份链调整后本地验证通过 | Monitor 67 passed；LCS 与 E2E 结果须绑定各自最后修改重新回读 |
| Git commit | 本轮提交承载本地实现 | 最终 SHA 以 Git 回读为准 |
| Git push | 本轮已授权推送 `main` | 最终状态以 `origin/main` 回读为准 |
| Cloud Build | 本轮未提交、未推送、未批准执行 | 仓库 Trigger 脚本要求人工审批；云端 Trigger 当前配置尚未回读验证 |
| Cloud Run 候选 | 未创建 | Cloud Build 未批准、未执行 |
| IAP 登录验收 | 未执行 | 无候选 |
| 业务验收 | 未执行 | 无真实新数据链 |
| 生产流量 | 未改变 | 未部署、未切流 |
| BigQuery/Firestore/Logging/IAM | 未修改 | 本轮禁止云写 |

“本地测试通过”只能更新第一行，不能把其余行提前写成完成。

整体结论：**尚未完成**。未完成项是第 5、6 节中需要新授权和真实环境证据的
BigQuery/Firestore/Logging/IAM 修改、LCS/Monitor 候选、IAP 登录验收、业务验收与
生产流量；本轮没有执行这些事项。
