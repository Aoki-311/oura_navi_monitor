# LCS 与 OurA Navi Monitor 三小时刷新最终收口手册

更新日：2026-08-31

## 1. 真正目标

这次要交付的不是“把一个定时器从 15 分钟改成 3 小时”，而是以下完整能力：

1. LCS 遇到失效父消息时，不再一直返回“回答无法生成”，也不会把问题接到未来、
   当前或不确定的轮次；恢复后用户消息和回答都能真正保存，刷新页面仍然存在。
2. Monitor 能连续接收旧 v1 和新 v2 事件；可隔离记录、生命周期晚到或单轴未计测只影响
   对应记录/测量轴。只有 manifest 对账和事实主键完整性损坏才阻断整批发布；未登记 revision
   继续留下诊断，但不能冻结正常用户数据和 watermark。
3. 缺失的两天由同一个 Refresh Job 按固定目标水位补齐，并做到来源事件、正式事实、
   去重和隔离逐项对账；不能靠手工插 0 或只看水位前进。
4. 稳态只有一个自动发布 owner：`oura-navi-monitor-refresh`。新 Scheduler 每 3 小时
   运行一次；旧 15 分钟 Scheduler 永久保持暂停。
5. 旧 BigQuery scheduled query 只有在新链连续运行、依赖为零并形成证据收据后才暂停
   自动调度。配置、旧表和 raw 表继续保留观察，不在本次删除。

新 Scheduler 为 `oura-navi-monitor-refresh-three-hour`，日本时间每天
`00:05、03:05、06:05、09:05、12:05、15:05、18:05、21:05` 运行。3 小时不是
“页面三小时才变一次”：页面随每个成功发布批次更新，只显示业务需要的 `dataThrough`、
当天是否为部分日以及最新运行是否失败；前端不展示刷新频率或下一计划时间。

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

- 带 `published_run_id` 的 `dashboard_events_v2` / `dashboard_user_list_v2` 是唯一正式
  run-bound reader owner；没有旧函数兼容 wrapper，也没有 Firestore 实时名单 fallback。
- additive schema/函数发布与旧对象 retirement 分开执行；先验证 v2 与已发布
  `user_scope`，再精确删除旧函数和旧 dashboard 对象。
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
- 旧 DTS 暂停前要求三次不同窗口、不同 execution ID；每次都必须同时证明 Execution 的
  `creator` 是精确 Scheduler OAuth SA，并由同一 SA 对精确 Job 发出的 RunJob audit 通过
  LRO `response.metadata.name` / `response.response.name` 指向该精确 Execution。任何字段
  缺失都关闭失败，不能用时间邻近或三次手工执行冒充。
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
8. LCS 无流量 candidate；六条业务问答路由逐条验证 trace+span，四条 debug 路由
   单独验证为非业务流量；
9. 用一条真实业务 candidate 样本，把精确 `revision+trace+span+route` 的 automatic HTTP
   与 `monitor.v2 question_received` 证明注册进唯一 revision ledger；
10. LCS 流量（独立授权，且第 9 项注册 receipt 是前置 STOP 门）；
11. 等待 LCS promotion receipt 指定的旧请求 drain 完成；
12. 以 receipt 原始 bytes、live 100% traffic readback 单独激活 v2 enforcement；
13. 旧 Scheduler 冻结；
14. Refresh Job 更新和新 Scheduler 创建；
15. 两天补数，并证明 expected LCS revision 的精确 HTTP/event 门全部为 0；
16. Monitor 0% candidate 的 IAP/API/浏览器/业务验收；
17. 新 Scheduler 启用；
18. 三次正式 Scheduler 运行与依赖审计；
19. 旧 DTS 自动调度暂停；
20. DTS 暂停后的 45 分钟与 72 小时观察；
21. Monitor 最终流量；
22. 最终 readback 再确认旧 API 函数与旧 dashboard 对象均不存在。

所有会读取或改动 GCP 的运维入口都必须显式传入同一个批准 key 的规范化绝对路径；路径必须
是当前用户拥有、权限精确为 `0600` 的普通文件，symlink 与任何其他权限一律拒绝。脚本只把
凭据逐命令传给 Google 子进程，不在 shell 全局 `export`，不用 ADC fallback，也不把 key 放进
镜像、收据或文档：

```bash
<COMMAND> --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

## 5. 唯一执行顺序

所有脚本先不带 `--apply` 运行 plan，核对精确目标和确认串，再对同一参数单独授权
apply。plan 只渲染本地参数、精确目标与确认串，不联网、不读取 credential、更不产生
mutation；需要云端事实的 authenticated read-only preflight 是后续独立步骤，不能把 plan
的成功当成云端验证。下列 `<...>` 必须来自本次只读 inventory，不允许猜。

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

执行 Scheduler activation、旧 DTS pause 与 Monitor promotion 的批准 operator 还必须对
命名数据库 `lcs-user-data`、集合 `monitor_release_locks` 拥有精确 Firestore transaction
read/create/set/delete 权限（可由受控的 `roles/datastore.user` 或等价最小自定义角色提供）。
三个流程共用该集合；Scheduler activation 与 DTS pause 共用同一 `refresh-chain` 文档域。
不同本地 receipt 路径产生的不同 intent 必须 STOP，不能互相覆盖。

### B. 提交并构建 Monitor 无流量候选

正式构建必须来自 clean Git commit，使用完整 40 位 SHA。`cloudbuild.yaml` 会运行 Python
全量、Shell、JS、Docker 内浏览器 E2E，推送完整 SHA tag，解析 digest，以
`<MONITOR_RUNTIME_SA>` 创建 `candidate` tag 且 `--no-traffic`，随后回读 revision、digest、
Git SHA、Ready、身份和 0% 流量。

先发布 additive base/source/fact/schema 和两个正式 table function，执行一次 Refresh，
并证明 published receipt、run-bound `user_scope` 与两个 v2 真实读取全部通过；随后渲染并
精确执行 `sql/retire_legacy_api_objects.sql`。退役前不得删除，退役后不得保留旧函数当作
fallback。

三个入口按下列顺序先运行 plan；每个 apply 都是 BigQuery 写操作，必须对同一参数单独
批准、逐命令注入批准 key 并加 `--apply`：

```bash
./scripts/bootstrap_monitor_data.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"

./scripts/publish_monitor_source_views.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"

./scripts/publish_monitor_views.sh \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --python .venv/bin/python \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

发布后必须回读 `pipeline_state` 的 lease 字段、两张逐事件 ledger、质量账，以及兼容
wrapper 与两个 `*_v2` runtime routine 的对象类型和参数；缺一项就不能进入冻结。已有完整
v2 published run 时可立即生成下述收据；从 legacy schema 迁移时，必须先完成 F 的首个原子
refresh，再生成包含同 run projection/指纹与四条真实 routine 读取的不可覆盖收据。收据在
Monitor promotion 前必须完成，不能用只有 DDL 对象的检查冒充：

```bash
.venv/bin/python scripts/verify_monitor_data_contract.py \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --expected-git-sha "<FULL_40_CHARACTER_GIT_SHA>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --receipt-output "<NEW_ABSOLUTE_SCHEMA_RECEIPT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --verify
```

验证器会在 published 水位附近真实执行 runtime 唯一读取的 `dashboard_events_v2` /
`dashboard_user_list_v2`，并绑定已发布 `published_run_id` 核对服务实际消费的输出字段；
只看 routine 名称或类型不能通过。两条读取使用 64 MiB 的独立费用硬上限，不会
借验收脚本放开无界扫描。

additive DDL 对同一张表的新增字段必须合并为一次 metadata update，避免触发 BigQuery
短时间表更新配额；任何 DDL 失败都先读回已有字段，再安全重跑同一个幂等入口。

### C. 先让两个 0% candidate 对齐，并独立决定 LCS 流量

Monitor additive schema/source view、0% candidate 与 Refresh Job 合同就绪后，构建并验证
LCS 0% candidate。LCS 必须证明：原受影响路径能生成答案；stale parent 最多恢复一次；
刷新后用户与 assistant 消息仍存在；六条真实业务问答路由的 question/answer/persistence
都带同一 `revision + trace + span`；四条 debug 路由标为 `debug_*` 且不进入业务指标；
服务器写入失败也不能抹掉 final 正文。不能用“至少一条日志”代表全路由通过。

从六条业务样本中选择一条 2xx candidate 请求，先只读 plan，再使用 plan 输出的精确确认串
apply。这个入口同时查询 Cloud Run automatic HTTP request log 与 producer event source；只有同一
`revision+trace+span+route` 各恰好一条、event 明确声明 `monitor.v2`，才会登记 expected
revision 并写不可覆盖 receipt。它不接受 debug 样本，也不接受超过两小时的查询窗口：

```bash
.venv/bin/python scripts/register_monitor_v2_revision.py \
  --project "<PROJECT_ID>" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --revision "<EXACT_LCS_CANDIDATE_REVISION>" \
  --trace "projects/<PROJECT_ID>/traces/<32_LOWERCASE_HEX>" \
  --span "<16_LOWERCASE_HEX>" \
  --endpoint-class "<ask_OR_ask_stream>" \
  --window-start "<EXACT_UTC_WINDOW_START_Z>" \
  --window-end "<EXACT_UTC_WINDOW_END_Z>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时加入 `--receipt-output <NEW_ABSOLUTE_V2_REGISTRATION_RECEIPT_JSON>`、plan 输出的
`--confirm-register` 与 `--apply`。没有这份 receipt，或 ledger 读回不是同一 revision/proof
hash，禁止给 LCS candidate 切生产流量。定时刷新只读这个 ledger，不能根据运行中偶然出现
的一条 event 自行把 revision 升级成 v2 权威。

registration 只让这个已证明的 immutable candidate revision 自身立即进入严格
trace/span/route/version 门；它不启动“所有未知 revision 阻断”。因此样本产生到正式切流
之间，旧生产 revision 的正常 2xx 仍只记 legacy coverage，不会让首轮迁移自我卡死。

随后在 LCS 仓库对同一 candidate 单独授权并运行 `backend/tools/promote_candidate.sh`，只接受
`receiptType=lcs_candidate_promotion_v2` 的不可覆盖 promotion receipt。receipt 必须包含精确
project/region/service/targetRevision、`serviceAfter` 仅目标 revision=100%、
`trafficReadbackAt`、排序稳定的 `oldPositiveRevisions`（revisionName/percent/timeoutSeconds）、
`maxRequestTimeoutSeconds` 与按其计算的 `drainUntil`。promotion 成功不等于已激活 Monitor
enforcement；必须先等当前 UTC 时间达到 `drainUntil`，让旧 revision 的在途最长请求退出。

drain 后先 plan，再用 plan 给出的精确确认串 apply：

```bash
.venv/bin/python scripts/activate_monitor_v2_enforcement.py \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --service "lcs-rag-app" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --promotion-receipt "<ABSOLUTE_LCS_PROMOTION_V2_RECEIPT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时加入 `--receipt-output <NEW_ABSOLUTE_V2_ENFORCEMENT_RECEIPT_JSON>`、plan 输出的
`--confirm-activate` 与 `--apply`。脚本先按原始 bytes 计算 promotion receipt SHA-256，校验
receipt 内时间与旧 traffic/timeout，再用 60 秒上限现场 `gcloud run services describe`；live
traffic 仍必须精确只有 target revision=100%。最后才在 BigQuery 事务内对同一 ledger row
一次性写入 `enforcement_start=CURRENT_TIMESTAMP()`、固定 activation source、promotion receipt
SHA 与 live readback SHA。已激活、部分写入、坏 registration/activation 行都拒绝；定时任务
没有写权威版本或 cutover 时间的路径。

质量 SQL 只从合法 registration/activation ledger 行决定哪些 revision 可以启用严格的
HTTP correlation 计测。未登记 revision、缺 trace/span 或路由关联异常始终保留为 coverage/
axis-unmeasured 诊断，不能让 recurring refresh 回滚整批。activation 后第一轮正式补数/刷新
仍须证明 expected revision 的 2xx HTTP 与 question event 能精确对应；该证明用于发布验收，
不再充当全体用户数据的运行时开关。旧未登记 revision 不冒充严格 v2 计测，也不删除其可证明
的基础事实。Monitor Web 仍保持 0%，直至三周期、
DTS 暂停观察和登录业务验收全部完成。

### D. 先冻结旧 15 分钟 Scheduler

```bash
./scripts/cutover_refresh_scheduler.sh \
  --stage freeze-old \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --snapshot-output "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时再加入 plan 输出的 `--confirm-cutover` 和 `--apply`。脚本暂停旧 Scheduler，
重新读回 PAUSED，并确认现有 Job execution 全部终态、canonical BigQuery DML 为空。
如果旧镜像没有 lease，但 execution 仍在运行，也会停止。

### E. 用同一 candidate digest 部署 Refresh Job，创建暂停的新 Scheduler

```bash
./scripts/bootstrap_gcp.sh \
  --stage activate \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --runtime-service-account "<REFRESH_WRITER_SA>" \
  --scheduler-invoker-service-account "<SCHEDULER_INVOKER_SA>" \
  --image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --analytics-start-at "<ANALYTICS_START_AT>" \
  --deploy-receipt-output "<NEW_ABSOLUTE_JOB_DEPLOY_RECEIPT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时逐命令注入批准 key，并加入 plan 输出的精确 `--confirm-activate` 与 `--apply`。
只有旧、新 Scheduler 都不是 ENABLED 时才
允许更新 Job。新 Scheduler 首次创建后立即暂停，再写入正式
`5 */3 * * * / Asia/Tokyo`；最终必须读回 PAUSED、60 秒 deadline、0 retry、精确 URI
和 `<SCHEDULER_INVOKER_SA>`。脚本用不可覆盖 receipt 保存 Job image、writer identity、
Scheduler identity、环境和两个资源的实际 readback；后续 backfill 必须消费这张 receipt。

随后用同一 snapshot 做完整冻结复核：

```bash
./scripts/cutover_refresh_scheduler.sh \
  --stage freeze \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --snapshot-output "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

### F. 在两个 Scheduler 都暂停时追平缺口

```bash
./scripts/backfill_recent_data.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --freeze-snapshot "<ABSOLUTE_FREEZE_SNAPSHOT_JSON>" \
  --job-deploy-receipt "<ABSOLUTE_JOB_DEPLOY_RECEIPT_JSON>" \
  --receipt-output "<ABSOLUTE_BACKFILL_RECEIPT_JSON>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --expected-job-service-account "<REFRESH_WRITER_SA>" \
  --expected-old-scheduler-service-account "<OLD_SCHEDULER_SA>" \
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时加 plan 输出的 `--confirm-backfill` 与 `--apply`。任务从已发布水位按最长 24 小时
分段追到一个固定目标；缺口是两天就补两天，实际更长就补完整缺口。成功标准不是“Job
绿了”，而是：固定目标到达、lease 释放、发布 run 成功、manifest 与正式事实逐哈希
对账、blocking quality=0。duplicate 可以非零，但每一条都必须在本次逐 run manifest 中
精确对应 `deduplicated`，或作为 `conflicting_duplicate_event_id` 对应
`row_quarantined`；`pipeline_runs.duplicate_rows` 与这两类 durable disposition 的总数必须
相等。脚本还必须从本次 Cloud Run execution 本身回读相同 digest、writer identity 和
terminal success，不能只相信执行前的可变 Job template。

### G. 保持 0% candidate，验证唯一 reader contract

本次必须用最终提交 SHA 和真实线上 revision 重新盘点。candidate 在数据验收完成前
保持 0%；先验证唯一 v2 routine 能按 published run 同时读取历史与当前事实，再通过
candidate tag 验证历史两天、部分日、失败质量
提示、用户详情、三个独立分析轴和导出。保存绑定精确 revision/digest/SHA/身份的 API、
登录浏览器与业务验收证据，但此阶段禁止切 traffic。

如果只读 inventory 发现 runtime 依赖任何 legacy reader 或第二套名单来源，立即 STOP；
不能用 fallback 掩盖 canonical contract 不完整。

### H. 启用新三小时 Scheduler

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
  --expected-new-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 后必须读回：旧 Scheduler=PAUSED、新 Scheduler=ENABLED、精确 cron/timezone/URI/
identity/deadline/retry；保存不可覆盖 activation receipt 和 `canonical_start_at`。

### I. 观察三个正式窗口，完成依赖收据

三次运行必须来自三个正式 Scheduler slot，拥有不同 execution ID、不同窗口，并同时满足
精确 `AttemptFinished` target、Execution `creator` 和 RunJob audit principal/job/LRO
execution name；每个 Cloud Run execution 本身都必须回读 activation receipt 中的相同
image digest、writer identity 和 terminal success。现实至少跨 6 小时，通常需要约 6–9
小时；即使手工执行的时间刚好邻近 Scheduler Attempt，也不能冒充正式运行。

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

### J. 只暂停旧 DTS 自动调度

先运行完整只读 preflight。它会重新核对 transfer、query SHA、DTS writer、Job、Scheduler、
三次正式 execution、成功的 `AttemptFinished`、在途 DTS run 和旧表 inventory，并生成一张
不可覆盖的 preflight receipt；这一步不会调用 `bq update`：

```bash
./scripts/pause_legacy_bigquery_refresh.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --transfer-config "<EXACT_TRANSFER_CONFIG_RESOURCE>" \
  --canonical-start-at "<ACTIVATION_CANONICAL_START_AT>" \
  --expected-query-sha256 "<EXACT_LEGACY_QUERY_SHA256>" \
  --expected-dts-service-account "<DTS_WRITER_SA>" \
  --expected-scheduler-service-account "<SCHEDULER_INVOKER_SA>" \
  --dependency-receipt "<ABSOLUTE_DEPENDENCY_RECEIPT_JSON>" \
  --activation-receipt "<ABSOLUTE_ACTIVATION_RECEIPT_JSON>" \
  --preflight-receipt-output "<ABSOLUTE_DTS_PREFLIGHT_RECEIPT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --preflight
```

preflight 成功后 60 分钟内，用完全相同的 target/identity/receipt 参数再次运行，加入
`--preflight-receipt`、受控的 `--snapshot-output`、plan 输出的精确
`--confirm-pause` 和 `--apply`。首次 apply 会先在该路径原子写入不可篡改的 intent，再调用
`--no_auto_scheduling`；成功读回后，才在同一路径原子替换为 final receipt。apply 会再次执行
全部只读门，不会因为已有 preflight 而跳过：

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
  --activation-receipt "<ABSOLUTE_ACTIVATION_RECEIPT_JSON>" \
  --preflight-receipt "<ABSOLUTE_DTS_PREFLIGHT_RECEIPT_JSON>" \
  --confirm-pause "<TRANSFER_CONFIG>:pause-after-canonical-3:<ACTIVATION_CANONICAL_START_AT>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --apply
```

首次创建 intent 时，preflight receipt 超过 60 分钟、其依赖/activation hash 改变，或任何
live readback 变化，都是 STOP，必须重新 preflight。若 DTS 已成功 disabled、但命令回包、
读回或 final receipt 写盘中断，必须用完全相同的参数和同一个 `--snapshot-output` 重跑；脚本
会验证 intent 内保存的 preflight hash 与“intent 创建时未超过 60 分钟”的时间关系，再确认
当前 transfer 除 `disabled` 外的受控配置与 intent 完全一致，然后只读补齐 final，不要求已经
过期的 preflight 重新变新，也不会再次暂停或自动回滚。已有完整 final 时，同样参数重跑只做
幂等验收。intent/final 篡改、transfer 配置漂移、仍在途的 DTS run 或旧表变化都是 STOP。
脚本只调用 `--no_auto_scheduling`；不删除 transfer config、旧表、raw、告警或日志指标。

暂停至少 45 分钟后运行：

```bash
./scripts/verify_legacy_bigquery_pause.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --pause-snapshot "<ABSOLUTE_DTS_PAUSE_SNAPSHOT_JSON>" \
  --receipt-output "<ABSOLUTE_DTS_45M_RECEIPT_JSON>" \
  --min-observation-minutes 45 \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --verify
```

72 小时后换一个新 receipt 路径，以 `--min-observation-minutes 4320` 再跑一次。两次都要
证明 DTS 仍 disabled、没有新/in-flight transfer run、四张旧表时间和行数没变、canonical
Job/Scheduler/digest/身份没变且水位继续新鲜。

### K. 72 小时通过后才切 Monitor traffic

先重新通过 candidate tag 验证 API/IAP/登录浏览器/历史数据/导出和业务场景，并再次回读
candidate 仍为 Ready、相同 SHA/digest/runtime identity、production traffic=0%。promotion
脚本还会在切流前现场复核 canonical Job 的 digest/identity/命令、三小时 Scheduler 合同以及
旧 DTS 仍 disabled；它不只相信 72 小时收据中的旧快照。随后运行：

```bash
./scripts/promote_candidate.sh \
  --project "<PROJECT_ID>" \
  --region "<REGION>" \
  --service "oura-navi-monitor" \
  --dataset "<DATASET_ID>" \
  --location "<BQ_LOCATION>" \
  --source-service "lcs-rag-app" \
  --revision "<EXACT_CANDIDATE_REVISION>" \
  --expected-image "<EXACT_MONITOR_IMAGE_AT_SHA256>" \
  --expected-git-sha "<FULL_40_CHARACTER_GIT_SHA>" \
  --expected-build-id "<EXACT_CLOUD_BUILD_ID>" \
  --expected-service-account "<MONITOR_RUNTIME_SA>" \
  --expected-job-service-account "<EXACT_REFRESH_JOB_SA>" \
  --legacy-transfer-resource "<EXACT_LEGACY_DTS_TRANSFER_RESOURCE>" \
  --firestore-database "lcs-user-data" \
  --release-lock-collection "monitor_release_locks" \
  --schema-receipt "<ABSOLUTE_SCHEMA_RECEIPT_JSON>" \
  --api-receipt "<FRESH_ABSOLUTE_AUTHENTICATED_API_RECEIPT_JSON>" \
  --backfill-receipt "<ABSOLUTE_BACKFILL_RECEIPT_JSON>" \
  --acceptance-receipt "<FRESH_ABSOLUTE_ACCEPTANCE_RECEIPT_JSON>" \
  --activation-receipt "<ABSOLUTE_ACTIVATION_RECEIPT_JSON>" \
  --dts-pause-snapshot "<ABSOLUTE_DTS_PAUSE_SNAPSHOT_JSON>" \
  --dts-45m-receipt "<ABSOLUTE_DTS_45M_RECEIPT_JSON>" \
  --dts-72h-receipt "<ABSOLUTE_DTS_72H_RECEIPT_JSON>" \
  --snapshot-output "<NEW_ABSOLUTE_PROMOTION_SNAPSHOT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>"
```

apply 时加入 plan 输出的精确 `--confirm-promotion` 与 `--apply`。脚本要求 activation、DTS
pause、45 分钟与 72 小时收据全部属于同一 image/canonical start/pause snapshot 链；切流
前再次拒绝 candidate 的任何正 production traffic，切流后只接受目标 revision=100%，其他
revision 正流量为 0。切流后还要对正式 URL 重新做一次 API/IAP/浏览器/业务验收。

锁没有自动过期和“超时抢锁”。同一 intent 的第二个 `pre` 或 `post` 进程也必须冲突，不能
假定原进程已经退出，更不能删除原进程仍在使用的锁。中断后先只读检查
`lcs-user-data/monitor_release_locks/<HASHED_DOCUMENT_ID>`、进程/流水线状态、现场
Scheduler/Job/DTS/Service/Revision/traffic 与本地 intent，确认没有活跃执行者且锁身份完全
一致。若现场仍为 `pre`，disposition 必须选 `aborted_pre`；若现场已经是 exact `post`，必须
选 `authorized_post_recovery`。先用原 snapshot 只生成 plan；这一步不读取 credential、
不访问云端，也不改变锁：

```bash
PYTHONPATH=. .venv/bin/python scripts/promotion_release_lock.py release \
  --promotion-state "<EXACT_ABSOLUTE_PROMOTION_SNAPSHOT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --intent-disposition "<aborted_pre|authorized_post_recovery>"
```

只有得到独立的云写授权后，才可对完全相同的参数加入 plan 输出的精确确认串、
`--allow-intent-release` 与 `--apply`：

```bash
PYTHONPATH=. .venv/bin/python scripts/promotion_release_lock.py release \
  --promotion-state "<EXACT_ABSOLUTE_PROMOTION_SNAPSHOT_JSON>" \
  --credential-file "<ABSOLUTE_APPROVED_KEY_JSON>" \
  --intent-disposition "<EXACT_PLAN_DISPOSITION>" \
  --allow-intent-release \
  --confirm-intent-release "<EXACT_PLAN_CONFIRMATION>" \
  --apply
```

人工处理不会把锁文档直接删除，而是在同一个 service 锁文档中原子写入绑定 intent hash 的
退役记录。`aborted_pre` 永久拒绝同一个旧 intent；只有携带新收据、产生不同 intent hash 的
新 snapshot 才能替换该退役记录。`authorized_post_recovery` 只允许完全相同的 intent 在
pre-lock 与 held-lock 都仍为 `post` 时消费一次并补齐 final，绝不能进入 `update-traffic`。
本地已有不可变 `final` 时，脚本
允许 exact final recovery，只做锁内重读、清锁和 already-complete 返回。不同 intent、无法
证明原执行者已退出、或任何现场漂移一律 STOP；禁止按锁年龄判断失效。

API 与登录浏览器收据的 60 分钟新鲜度只授权一次新的 `pre -> traffic=100%` 突变。脚本在
取得锁并完成全部慢速现场重读后，紧贴 `update-traffic` 前再次校验时间；校验同时重新计算
API/acceptance 原始 bytes 的 SHA-256，必须仍与 durable intent 完全一致。此门失败时保留锁。
`post`/`final` 的受控收尾不再授权新切流，所以不会因为收据已超过 60 分钟而重做或回滚，
但仍须重新核对 Scheduler、Job、DTS、Service、Revision 和 receipt hash。

## 6. STOP 条件

- candidate 的 runtime identity 与冻结的当前 Monitor runtime 不一致：停止候选部署。
- Refresh writer 与 Scheduler invoker 缺失、相同或权限不精确：停止 Job/Scheduler 激活，
  但这不阻止 main push 自动生成待审批 source build。
- 真实 Job、Scheduler、DTS、image digest、SHA、service account 与计划不一致：停止并
  重新 inventory。
- 任一 Scheduler 未暂停、任一 Job execution/BQ DML 仍在运行：不部署 Job、不补数。
- 补数只前进水位但 manifest/fact 对不上，或有 blocking quality：不启用新 Scheduler。
- Monitor additive source view、Refresh Job 合同或 0% candidate 尚未验证：不让 LCS v2
  writer 获得生产流量；Monitor Web 是否已有生产流量不是这个数据合同的替代证据。
- expected LCS revision 没有一条真实 2xx automatic HTTP 与 `monitor.v2 question_received`
  的精确 candidate registration receipt，或 receipt/ledger 的 proof hash 不一致：不切 LCS
  流量；定时任务不能自行注册 revision。
- LCS promotion receipt 不是 `lcs_candidate_promotion_v2`、字段/原始 SHA 不匹配、旧 traffic
  timeout 账不平、`drainUntil` 尚未到、live service 不再精确 target=100%，或 gcloud readback
  超时/失败：不激活 enforcement、不补数。
- enforcement ledger row 不是由唯一合法 registration + promotion receipt + live readback
  一次写成，或已存在/部分写入：停止；不能手改时间、跳过 drain 或让定时任务自注册。
- LCS expected revision 任一业务路由缺少 trace/span，出现一请求多事件/多请求一事件，或
  出现 2xx 无 question、event 无 completed HTTP、路由错标、contract 降级：该 revision
  不通过正式发布验收；周期 refresh 仍记录 coverage/axis-unmeasured，并继续发布其他正常事实。
- 三个运行不是 Scheduler-proven 正式窗口：不暂停旧 DTS。
- 任一正式 execution 的实际 digest/identity/terminal state 不匹配，或匹配到失败的 Scheduler
  Attempt：不暂停旧 DTS。
- 依赖、Data Access 覆盖或外部 owner 有未知项：不暂停旧 DTS。
- activation、DTS pause、45 分钟、72 小时 receipt 任一不属于同一链：不切 Monitor traffic。
- IAP/业务验收或 traffic readback 缺失：不能宣称生产修复完成。
- `delete_obsolete_monitor_resources.sh --apply` 现在必然失败；本次不删除旧对象。

## 7. 完成定义

只有以下证据全部存在，才能说“彻底修复完成”：

- 原用户故障在 LCS candidate 和生产都不再复现，刷新后回答仍可读；
- 两天及全部真实缺口进入 canonical，来源/事实/隔离/去重逐项对账；
- Monitor 三页显示正确 `dataThrough`、部分日和最新失败，不再画假 0；
- 问题主题、产品、依頼任务分别显示 measured/partial/not_measured；
- 当前 canonical reader 保持生产可读；LCS v2 writer 的六条业务路由先完成精确
  revision+trace+span 端到端验证；72 小时门通过后新 Monitor revision 才接管 Web 流量；
- 旧 15 分钟 Scheduler=PAUSED，新三小时 Scheduler=ENABLED；
- 三次 Scheduler-proven 正式运行成功；
- 依赖收据为零，旧 DTS 自动调度 disabled；45 分钟和 72 小时观察通过；
- Git SHA、Cloud Build、revision、image digest、runtime identity、IAP 验收和 traffic 分别
  有独立 readback 证据。

本地测试通过只能证明代码候选具备这些门禁；在云端步骤实际执行并读回前，生产状态仍为
`未完成 / STOP`。
