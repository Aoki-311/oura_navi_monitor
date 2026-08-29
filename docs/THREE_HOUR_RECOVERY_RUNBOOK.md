# LCS 与 OurA Navi Monitor 三小时刷新最终收口手册

更新日：2026-08-29

## 1. 真正目标

这次要交付的不是“把一个定时器从 15 分钟改成 3 小时”，而是以下完整能力：

1. LCS 遇到失效父消息时，不再一直返回“回答无法生成”，也不会把问题接到未来、
   当前或不确定的轮次；恢复后用户消息和回答都能真正保存，刷新页面仍然存在。
2. Monitor 能连续接收旧 v1 和新 v2 事件；晚到事件、坏记录、产品、问题主题、依頼任务
   分别处理，不再因为一条记录或一个分析轴异常让整批、整页消失。
3. 缺失的两天由同一个 Refresh Job 按固定目标水位补齐，并做到来源事件、正式事实、
   去重和隔离逐项对账；不能靠手工插 0 或只看水位前进。
4. 稳态只有一个自动发布 owner：`oura-navi-monitor-refresh`。新 Scheduler 每 3 小时
   运行一次；旧 15 分钟 Scheduler 永久保持暂停。
5. 旧 BigQuery scheduled query 只有在新链连续运行、依赖为零并形成证据收据后才暂停
   自动调度。配置、旧表和 raw 表继续保留观察，不在本次删除。

新 Scheduler 为 `oura-navi-monitor-refresh-three-hour`，日本时间每天
`00:05、03:05、06:05、09:05、12:05、15:05、18:05、21:05` 运行。3 小时不是
“页面三小时才变一次”：页面随每个成功发布批次更新，并明确显示 `dataThrough`、
下一计划时间、当天是否为部分日、最新运行是否失败。

## 2. 根因白话说明

### 2.1 LCS 为什么会“回答无法生成”

原请求带着一个已经失效的父消息编号。旧恢复逻辑会从整个会话里找“最后一轮”，并不
知道本次问题所在的时间上界；多标签页或并发写入时，它可能接到未来回答或当前轮次。
现在只允许选择“本次用户消息之前、唯一且完整的最近一轮”。找不到、时间相同无法判定、
当前轮已经完成或出现未来轮次时，都安全改为独立问题；前端最多重试一次，原请求除
parent 外保持不变，并在远端保存后重新读取验证。

### 2.2 Monitor 为什么这几天显示不好

这次“上线后整页没有数据”的直接触发点，不是事实表突然被删了，而是读取合同写反了：
每个分析 API 为了读取一个稳定的 `dataThrough`，同时强制查询三张新增诊断表。线上先有
旧 revision、后有不完整 schema 时，任一诊断表不存在都会让整个 API 返回 500，已经
存在的 3,334 条问题和 3,218 条回答也一起无法返回。现在稳定发布水位只读
`pipeline_state`；运行质量和逐事件诊断是独立的可选读取。诊断不可用时，API 明确返回
`diagnosticsStatus=unavailable`，但正文、趋势和用户详情继续保留。

另一个旧兼容路径是 HTTP 缓存：旧代码只禁止缓存 HTML/JS，没有禁止浏览器复用分析
API 的 GET 响应。现在服务端对全部 `/api/` 返回 `no-store`，前端每次 `fetch` 也显式
使用 `cache: no-store`。所以 schema 或 revision 恢复后，不会继续显示修复前的空响应。

原链路混用了“日志到达时间”和“业务发生时间”。一条今天才到、但昨天发生的事件，
可能写进昨天分区，却躲过今天的去重、补充字段和质量检查；水位仍然向前，后续任务也
不会再处理它。与此同时，坏记录只有一个总数，没有可追踪、可重放的逐事件去向；质量
检查失败时，诊断明细还会跟着事务一起回滚。页面最后又把水位之后的日期补成 0，于是
“没发布”“没测量”和“真实没有使用”看起来完全一样。

现在每个源事件都有哈希化 manifest/issue 去向，晚到事件按本次实际事件分区处理，事实、
水位、运行收据和成功质量账在同一事务发布。若质量门阻断，事实回滚，但阻断诊断和失败
运行会独立保存；API/UI 继续展示上一次成功数据，同时明确提示最新运行失败。

## 3. 已形成的代码门禁

- `dashboard_events(start,end)` 与 `dashboard_user_list(start,today)` 保持原正式名称，
  直接 `CREATE OR REPLACE`；没有并行 `_v2` 语义 owner。
- additive schema/函数发布与 destructive cleanup 已分开；旧对象删除脚本的 apply 已
  永久硬停止。
- Refresh Job 单任务、单 writer、固定命令、30 分钟 timeout、一次 Job retry；Scheduler
  60 秒 attempt deadline、0 次 Scheduler retry，避免同一次时点被控制面重复触发。
- Monitor Web candidate 沿用并回读当前线上 runtime service account；代码 push 和自动
  生成待审批 build 不以新建 Web IAM 或修改 trigger 为前置条件。
- Refresh writer 与 Scheduler invoker 是后续数据切换的两个精确身份，激活前必须不同；
  Cloud Build 使用 trigger 已配置的构建/部署身份。
- Monitor Web candidate 与 Refresh Job 必须使用同一个完整 `@sha256` 镜像；Job、两个
  Scheduler、补数、激活和 DTS 暂停收据都绑定这个 digest 与精确身份。
- 冻结不仅看应用 lease，还等待 Cloud Run Job execution 终态，并检查没有运行中的
  canonical BigQuery DML。
- 两天补数显式传入固定 `--target-at`，并验证 manifest → question/answer/action facts、
  duplicate、quarantine、轴未计测与 blocking quality。
- 旧 DTS 暂停前要求三次不同窗口、不同 execution ID，并把 Scheduler Attempt 日志与
  Cloud Run execution 创建时间关联，不能用三次手工执行冒充。
- 暂停旧 DTS 前后核对 transfer identity、SQL SHA、service account、schedule、四张旧表
  和在途 run；暂停后另做至少 45 分钟与 72 小时只读观察。

## 4. 独立授权门

以下每一项都是独立状态变化，前一项成功不自动授权后一项：

1. 本地代码验证；
2. commit；
3. push；
4. 后续 Refresh writer / Scheduler invoker 如需新建或改权时的 IAM 变更；
5. Cloud Build；
6. additive BigQuery schema/函数；
7. Monitor 无流量 candidate；
8. 旧 Scheduler 冻结；
9. Refresh Job 更新和新 Scheduler 创建；
10. 两天补数；
11. Monitor IAP/业务验收与流量；
12. LCS 无流量 candidate、真实问答和 v2 事件验收；
13. LCS 流量；
14. 新 Scheduler 启用；
15. 旧 DTS 自动调度暂停；
16. 未来旧对象删除。

所有获准的 GCP 命令都逐命令注入同一个批准 key，不在 shell 全局 `export`，不用 ADC
fallback，也不把 key 放进镜像、收据或文档：

```bash
CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="<ABSOLUTE_APPROVED_KEY_JSON>" \
GOOGLE_APPLICATION_CREDENTIALS="<ABSOLUTE_APPROVED_KEY_JSON>" \
<COMMAND>
```

## 5. 唯一执行顺序

所有脚本先不带 `--apply` 运行 plan，核对精确目标和确认串，再对同一参数单独授权
apply。下列 `<...>` 必须来自本次只读 inventory，不允许猜。

### A. 冻结现有发布身份与后续数据切换身份

先从本次只读 inventory 固定：

- `<MONITOR_RUNTIME_SA>`：当前 Monitor Web revision 实际使用的 runtime identity；候选
  继续使用它，不能凭空换成新账号；
- `<REFRESH_WRITER_SA>`：仅 Refresh Job 的 canonical BigQuery 写权限；
- `<SCHEDULER_INVOKER_SA>`：只能调用精确 Refresh Job；
- `<BUILD_DEPLOY_SA>`：从现有 GitHub trigger 读回的 Cloud Build 构建与部署身份；
- `<OLD_SCHEDULER_SA>`、`<DTS_WRITER_SA>`：从旧资源只读 inventory 得到，不改名猜测。

commit/push 和由 trigger 自动生成待审批 build 不要求先创建 IAM，也不修改 trigger。
只有进入 Refresh Job / Scheduler 激活时，才要求 writer 与 invoker 不同且权限精确；若
现有身份不能满足，创建账号和 IAM binding 是另一项云写操作，必须单独批准。

### B. 提交并构建 Monitor 无流量候选

正式构建必须来自 clean Git commit，使用完整 40 位 SHA。`cloudbuild.yaml` 会运行 Python
全量、Shell、JS、Docker 内浏览器 E2E，推送完整 SHA tag，解析 digest，以
`<MONITOR_RUNTIME_SA>` 创建 `candidate` tag 且 `--no-traffic`，随后回读 revision、digest、
Git SHA、Ready、身份和 0% 流量。

先只发布 additive base/source/fact/schema 和两个正式 table function。不要运行
`sql/retire_legacy_api_objects.sql`；旧 Monitor revision 尚有流量时尤其禁止 DROP。

三个入口按下列顺序先运行 plan；每个 apply 都是 BigQuery 写操作，必须对同一参数单独
批准、逐命令注入批准 key 并加 `--apply`：

```bash
./scripts/bootstrap_monitor_data.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python

./scripts/publish_monitor_source_views.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python

./scripts/publish_monitor_views.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python
```

发布后必须回读 `pipeline_state` 的 lease 字段、两张逐事件 ledger、质量账，以及
`dashboard_events` / `dashboard_user_list` 的对象类型和参数；缺一项就不能进入冻结。
使用候选的完整 SHA 和同一个不可变 digest 生成不可覆盖的 schema 收据：

```bash
CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="<ABSOLUTE_APPROVED_KEY_JSON>" \
GOOGLE_APPLICATION_CREDENTIALS="<ABSOLUTE_APPROVED_KEY_JSON>" \
.venv/bin/python scripts/verify_monitor_data_contract.py \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --expected-git-sha "<FULL_40_CHARACTER_GIT_SHA>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --receipt-output "<NEW_ABSOLUTE_SCHEMA_RECEIPT_JSON>" \
  --verify
```

验证器会在 published 水位附近真实执行 `dashboard_events` 和
`dashboard_user_list`，核对服务实际消费的输出字段；只看 routine 名称或类型不能通过。
两条读取使用 64 MiB 的独立费用硬上限（当前 BigQuery 编译结果至少需要 30 MiB），不会
借验收脚本放开无界扫描。

additive DDL 对同一张表的新增字段必须合并为一次 metadata update，避免触发 BigQuery
短时间表更新配额；任何 DDL 失败都先读回已有字段，再安全重跑同一个幂等入口。

### C. 先冻结旧 15 分钟 Scheduler

```bash
./scripts/cutover_refresh_scheduler.sh \
  --stage freeze-old \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --snapshot-output "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>"
```

apply 时再加入 plan 输出的 `--confirm-cutover` 和 `--apply`。脚本暂停旧 Scheduler，
重新读回 PAUSED，并确认现有 Job execution 全部终态、canonical BigQuery DML 为空。
如果旧镜像没有 lease，但 execution 仍在运行，也会停止。

### D. 用同一 candidate digest 部署 Refresh Job，创建暂停的新 Scheduler

```bash
./scripts/bootstrap_gcp.sh \
  --stage activate \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --runtime-service-account "<REFRESH_WRITER_SA>" \
  --scheduler-invoker-service-account "<SCHEDULER_INVOKER_SA>" \
  --image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --analytics-start-at "<ANALYTICS_START_AT>"
```

apply 时逐命令注入批准 key 并加 `--apply`。只有旧、新 Scheduler 都不是 ENABLED 时才
允许更新 Job。新 Scheduler 首次创建后立即暂停，再写入正式
`5 */3 * * * / Asia/Tokyo`；最终必须读回 PAUSED、60 秒 deadline、0 retry、精确 URI
和 `<SCHEDULER_INVOKER_SA>`。

随后用同一 snapshot 做完整冻结复核：

```bash
./scripts/cutover_refresh_scheduler.sh \
  --stage freeze \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --snapshot-output "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>"
```

### E. 在两个 Scheduler 都暂停时追平缺口

```bash
./scripts/backfill_recent_data.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --freeze-snapshot "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --receipt-output "<ABSOLUTE_BACKFILL_RECEIPT_JSON>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>"
```

apply 时加 plan 输出的 `--confirm-backfill` 与 `--apply`。任务从已发布水位按最长 24 小时
分段追到一个固定目标；缺口是两天就补两天，实际更长就补完整缺口。成功标准不是“Job
绿了”，而是：固定目标到达、lease 释放、发布 run 成功、manifest 与正式事实逐哈希
对账、duplicate=0、blocking quality=0，所有剩余差异均有 durable disposition。

### F. 先让 Monitor reader 真正接管，再发布 LCS writer

用 Monitor candidate 验证历史两天、部分日、失败质量提示、用户详情、三个独立分析轴和
导出；另行批准 Monitor 流量，并回读旧 Monitor revision 为 0%。只有此时才能构建 LCS
candidate。LCS candidate 必须验证：

Monitor 流量只能通过精确候选门执行：

```bash
./scripts/promote_candidate.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --service "oura-navi-monitor" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --revision "<EXACT_CANDIDATE_REVISION>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --expected-git-sha "<FULL_40_CHARACTER_GIT_SHA>" \
  --expected-service-account "<MONITOR_RUNTIME_SA>" \
  --schema-receipt "<ABSOLUTE_SCHEMA_RECEIPT_JSON>" \
  --api-receipt "<ABSOLUTE_AUTHENTICATED_API_RECEIPT_JSON>" \
  --backfill-receipt "<ABSOLUTE_BACKFILL_RECEIPT_JSON>" \
  --acceptance-receipt "<ABSOLUTE_ACCEPTANCE_RECEIPT_JSON>" \
  --snapshot-output "<ABSOLUTE_PROMOTION_SNAPSHOT_JSON>"
```

apply 时加入 plan 输出的 `--confirm-promotion` 与 `--apply`。schema、API、backfill 和
页面/业务四张 receipt 都必须绑定同一 project/region/service/revision/digest/SHA/身份；
API 四个端点必须分别为 200，历史趋势和个人趋势必须可见，诊断状态必须明确；页面收据
还必须分别记录登录浏览器、历史数据和业务验收。脚本在切流前保存旧 traffic，切流后只
接受目标 revision=100%，其他 revision 正流量必须为 0；snapshot 是精确回滚依据。

- 原受影响路径能生成答案；
- stale parent 最多一次恢复，parent 外请求不变；
- 当前/未来/歧义 parent 安全 standalone；
- 用户与 assistant 消息远端 ACK 后刷新页面仍存在；
- internal、Web、成功、失败和写回均产生严格 v2 事件；
- 从 LCS Logging → Monitor source → manifest/facts → API → UI 跟踪至少一条脱敏样本。

LCS candidate 验收后，流量仍需另行批准，并只能通过 LCS 仓库的精确候选门执行：

```bash
backend/tools/promote_candidate.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --service "lcs-rag-app" \
  --revision "<EXACT_LCS_CANDIDATE_REVISION>" \
  --expected-image "<EXACT_LCS_IMAGE_AT_SHA256>" \
  --expected-git-sha "<FULL_40_CHARACTER_GIT_SHA>" \
  --expected-build-id "<CLOUD_BUILD_ID>" \
  --expected-service-account "<LCS_RUNTIME_SA>" \
  --acceptance-receipt "<ABSOLUTE_LCS_ACCEPTANCE_RECEIPT_JSON>" \
  --snapshot-output "<ABSOLUTE_LCS_PROMOTION_SNAPSHOT_JSON>"
```

apply 时加入 plan 输出的 `--confirm-promotion` 与 `--apply`。验收收据必须明确绑定并通过
Monitor reader、stale-parent、Firestore 刷新后回读、真实 v2 事件、登录和业务验收；
脚本在切流前保存旧 traffic，在切流后只接受目标 LCS revision=100%。

### G. 启用新三小时 Scheduler

```bash
./scripts/cutover_refresh_scheduler.sh \
  --stage activate \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --snapshot-output "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --backfill-receipt "<ABSOLUTE_BACKFILL_RECEIPT_JSON>" \
  --activation-receipt-output "<ABSOLUTE_ACTIVATION_RECEIPT_JSON>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>"
```

apply 后必须读回：旧 Scheduler=PAUSED、新 Scheduler=ENABLED、精确 cron/timezone/URI/
identity/deadline/retry；保存不可覆盖 activation receipt 和 `canonical_start_at`。

### H. 观察三个正式窗口，完成依赖收据

三次运行必须来自三个正式 Scheduler slot，拥有不同 execution ID、不同窗口，并能与
Scheduler Attempt 日志匹配。现实至少跨 6 小时，通常需要约 6–9 小时，不能手工连续跑
三次凑数。

依赖收据至少覆盖最近 30 天并包含：

- 当前所有相关代码仓库的 runtime 引用数；
- BigQuery View、Materialized View、Routine、Scheduled Query 等对象引用数；
- `INFORMATION_SCHEMA.JOBS*` 的 query/copy/export 引用数和调用身份；
- Data Access Audit Logs 的 Storage Read/TableData/BI 非 query 读取；
- Connected Sheets、Looker Studio、导出、周报/月报、外部脚本和 owner 书面确认；
- 精确 transfer resource、SQL SHA、DTS writer identity、location 和四张旧输出表。

`codeReferenceCount`、`bigQueryObjectReferenceCount`、`queryJobReferenceCount`、
`nonQueryReadReferenceCount`、`unknownConsumerCount` 任一非 0，审计覆盖不是 `verified`，
或 external owner 未确认，都是 STOP。这个收据不能只手工把数字改成 0；原始查询结果、
时间范围、调用人和 owner 证据应一起归档。

### I. 只暂停旧 DTS 自动调度

```bash
./scripts/pause_legacy_bigquery_refresh.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --transfer-config "<EXACT_TRANSFER_CONFIG_RESOURCE>" \
  --canonical-start-at "<ACTIVATION_CANONICAL_START_AT>" \
  --snapshot-output "<ABSOLUTE_DTS_PAUSE_SNAPSHOT_JSON>" \
  --expected-query-sha256 "<EXACT_LEGACY_QUERY_SHA256>" \
  --expected-dts-service-account "<DTS_WRITER_SA>" \
  --expected-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --dependency-receipt "<ABSOLUTE_DEPENDENCY_RECEIPT_JSON>" \
  --activation-receipt "<ABSOLUTE_ACTIVATION_RECEIPT_JSON>"
```

apply 时加入精确 `--confirm-pause` 与 `--apply`。脚本只调用
`--no_auto_scheduling`；不删除 transfer config、旧表、raw、告警或日志指标。暂停前后
任何在途 DTS run、资源字段漂移或旧表即时变化都会停止。

暂停至少 45 分钟后运行：

```bash
./scripts/verify_legacy_bigquery_pause.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --pause-snapshot "<ABSOLUTE_DTS_PAUSE_SNAPSHOT_JSON>" \
  --receipt-output "<ABSOLUTE_DTS_45M_RECEIPT_JSON>" \
  --min-observation-minutes 45 \
  --verify
```

72 小时后换一个新 receipt 路径，以 `--min-observation-minutes 4320` 再跑一次。两次都要
证明 DTS 仍 disabled、没有新/in-flight transfer run、四张旧表时间和行数没变、canonical
Job/Scheduler/digest/身份没变且水位继续新鲜。

## 6. STOP 条件

- candidate 的 runtime identity 与冻结的当前 Monitor runtime 不一致：停止候选部署。
- Refresh writer 与 Scheduler invoker 缺失、相同或权限不精确：停止 Job/Scheduler 激活，
  但这不阻止 main push 自动生成待审批 source build。
- 真实 Job、Scheduler、DTS、image digest、SHA、service account 与计划不一致：停止并
  重新 inventory。
- 任一 Scheduler 未暂停、任一 Job execution/BQ DML 仍在运行：不部署 Job、不补数。
- 补数只前进水位但 manifest/fact 对不上，或有 blocking quality：不启用新 Scheduler。
- Monitor reader 尚未有真实流量：不让 LCS v2 writer 获得生产流量。
- 三个运行不是 Scheduler-proven 正式窗口：不暂停旧 DTS。
- 依赖、Data Access 覆盖或外部 owner 有未知项：不暂停旧 DTS。
- IAP/业务验收或 traffic readback 缺失：不能宣称生产修复完成。
- `delete_obsolete_monitor_resources.sh --apply` 现在必然失败；本次不删除旧对象。

## 7. 完成定义

只有以下证据全部存在，才能说“彻底修复完成”：

- 原用户故障在 LCS candidate 和生产都不再复现，刷新后回答仍可读；
- 两天及全部真实缺口进入 canonical，来源/事实/隔离/去重逐项对账；
- Monitor 三页显示正确 `dataThrough`、部分日和最新失败，不再画假 0；
- 问题主题、产品、依頼任务分别显示 measured/partial/not_measured；
- Monitor reader 先接管生产，随后 LCS v2 writer 的真实事件端到端可见；
- 旧 15 分钟 Scheduler=PAUSED，新三小时 Scheduler=ENABLED；
- 三次 Scheduler-proven 正式运行成功；
- 依赖收据为零，旧 DTS 自动调度 disabled；45 分钟和 72 小时观察通过；
- Git SHA、Cloud Build、revision、image digest、runtime identity、IAP 验收和 traffic 分别
  有独立 readback 证据。

本地测试通过只能证明代码候选具备这些门禁；在云端步骤实际执行并读回前，生产状态仍为
`未完成 / STOP`。
