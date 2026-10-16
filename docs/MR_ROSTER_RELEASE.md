# MR 名单、历史数据与地图更新

本次发布使用 `user_list.xlsx` 的 `OurA-Naviユーザー管理` 和
`DMエリア追加 支店別対象管轄エリア `。工作簿及用户身份清单留在受控的本地目录，
不提交 Git，也不放入 Cloud Build 或镜像。

代码验证、commit 和 push 完成后，由用户执行 Cloud Build。以下云端步骤在取得
该次构建的真实 revision、Git SHA、build ID 和不可变 image digest 后执行；
本文件本身不是已执行的发布凭据。Cloud Build 成功不能代替名单同步或数据验收。

## 数据与展示合同

| 来源 | 现有字段及处理 |
|---|---|
| A エリア | `area`；MR 按原值规范化，不根据勤務地改写 |
| B 勤務地 | `workplace`；沿用现有文本规范化 |
| C 社員名 / D 邮箱 | `name` / `email`；按规范化邮箱匹配既有名单 |
| E 役割 | `role`；社員MR、コントラクトMR、本部メンバー等 |
| F MR经历 | `mr_experience`；DM/HCS 保存，本社/管理员沿用 `-` |
| G 備考 | `department`；转换为现有部门枚举 |
| H 额外职务 | 忽略；不保存、不新增字段或输入项 |
| I HCSチーム | `team`；仅 HCS 可填写和保存 |

2026-10-16 文件的输入基线为：管理名单 162、DM 64、HCS 74、本社 21、管理员 3；
在对应用户均启用时，Summary 全体 MR 为 138，用户分析为 159。保留既有用户的
启停状态，若读回的活动范围不同，报告实际原因，不为凑人数强行启用用户。

地图按エリア统计，三个 MR 标签共用地理划分。冲绳没有地图图形，其 2 名用户仍
计入九州。勤務地为新潟的 4 名 HCS 按名单 A 列计入北海道東北，新潟县图形归関東A。
东京和岛根的支店管辖细分不产生独立县级用户统计，也不把支店人数重复分摊到各县。

## 1. 构建前确认

1. 保存工作簿文件哈希，并运行不联网的导入计划：

   ```bash
   PYTHONPATH=. .venv/bin/python scripts/import_monitor_users.py ../user_list.xlsx
   ```

2. 确认 `sourceRows=162`、没有重复邮箱、`normalizedHeadquartersRows` 为 24 行
   （21 名本社和 3 名管理员），部门和范围计数符合上述基线。
3. 完成导入/同步、受控历史补齐、地图和三个 MR 标签的回归检查，再 commit、push。
4. 用户执行 Cloud Build。核对终态成功、候选 revision Ready、完整 Git SHA、
   build ID、前后端 build identity 及候选不可变镜像；生产流量应保持原分配。

## 2. 保存线上清单并暂停写入

本地 GCP 命令仅使用用户指定的
`/Users/lee/Downloads/VScode/credentials/lcs-developer-483404-9c869eeb70bd.json`，
通过 `--credential-file` 或单条命令的 `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE`
传入。Cloud Run Web、Job、Cloud Build 和 Scheduler 继续使用各自的运行身份。

1. 在仓库外新建本次发布的受控证据目录，保存服务流量、候选 revision、Refresh Job、
   每个实际触发器及其启停状态、已发布 run/watermark、名单差异摘要和时间。
   不保存问答正文；身份明细仅用于受控核对，不能提交仓库。
2. 目标为项目 `lcs-developer-483404`、区域 `us-central1`、Firestore 数据库
   `lcs-user-data`、BigQuery dataset `oura_navi_monitor`（`US`）。
3. 暂停所有实际指向 `oura-navi-monitor-refresh` 的启用中定时触发器。
   当前规范触发器资源名来自 `app/refresh_policy.py`：
   `oura-navi-monitor-refresh-three-hour`，实际小时计划为 `5 * * * *`。
   旧触发器和已暂停的资源保持暂停，不因名称或旧文档恢复它们。
4. 等待暂停前已开始的 Job executions 和相关 BigQuery 写入全部终止，确认没有
   未过期的 publisher lease。保存暂停状态、execution 列表、写入任务和 lease
   的读回证据；暂停 Scheduler 本身不代表在途任务已结束。
5. 历史补齐使用现有 `scripts/cutover_refresh_scheduler.sh` 的冻结证据：先运行
   `--stage freeze-old`，再运行 `--stage freeze`，沿用同一个仓库外
   `--snapshot-output`。填入本次读回的 Job、旧/新 Scheduler service account，
   先查看 plan，再以它输出的精确确认串执行 `--apply`。两个阶段只暂停既有
   触发器并核对在途写入，不部署 Job 或重建资源。已有手工暂停状态也必须读回。
   如果实际资源与此工具的合同不同，先报告差异，不能手工伪造冻结 JSON。

冻结文件必须包含 `project`、`region`、`dataset`、`location`、`source_service`、
`old_scheduler`、`new_scheduler`、两个 `expected_*_scheduler_service_account`、
`freeze_started_at`、`freeze_verified_at` 和空的 `active_bigquery_writers_at_freeze`，
且不能已经有 `activation_started_at`。补齐工具还会重新读取实时状态确认两个
触发器暂停、目标和身份相符、没有在途 Job。

不得在旧 Refresh 镜像仍可能运行时同步新部门。旧 producer 不支持新的 MR 范围，
可能覆盖刚更新的用户范围或使新用户无法进入统计。

## 3. 只更新现有 Refresh Job 镜像

1. 使用本次候选 Web 的 `.../oura-navi-monitor@sha256:...` 不可变 digest，
   更新既有 `oura-navi-monitor-refresh` Job 的 image。保留原 service account、
   command/args、环境变量、数据库、来源、分析起点、任务数、重试和超时。
2. 更新前后保存完整 Job 配置并比对；除 image 和平台自动生成的元数据外应保持一致。
   用 `scripts/validate_refresh_job.py` 验证镜像、运行身份、规范命令及环境合同。
3. 确认所有触发器仍暂停，没有 Job 在更新后自动启动。

这里不运行 `bootstrap_gcp.sh`，不重建 IAM、日志源、Scheduler 或已有数据集。
不使用已退役的 rebuild 流程，不手动修改 `pipeline_state` watermark。

## 4. 重新生成并应用名单差异

冻结后重新读取线上状态，不能复用构建前的 digest：

```bash
PYTHONPATH=. .venv/bin/python scripts/import_monitor_users.py ../user_list.xlsx \
  --sync --compare-current --credential-file '<APPROVED_CREDENTIAL_PATH>'
```

1. 对照工作簿哈希检查 `createUsers`、`updateUsers`、`unchangedUsers`、
   `outsideWorkbookUsers` 和字段变化统计。2026-10-16 只读差异曾为新增 78、
   更新 61、不变 23；它是核对线索，最终以本次冻结后的差异为准。
2. 使用完全相同的文件、参数和本次 `syncDigest` 执行：

   ```bash
   PYTHONPATH=. .venv/bin/python scripts/import_monitor_users.py ../user_list.xlsx \
     --sync --apply --actor '<ADMIN_EMAIL>' \
     --expected-sync-digest '<syncDigest>' \
     --credential-file '<APPROVED_CREDENTIAL_PATH>'
   ```

3. 确认 `readbackMatched=true`，再次 compare 得到新增 0、更新 0。
   逐字段核对工作簿中每个人；原 `roster_id`、身份绑定、标签、启停状态和创建时间
   必须保持。外部名单行不会自动删除；如出现额外行，报告而非擅自删除。
4. 确认 H 列没有进入存储，74 名 HCS 的チーム与规范化后的 I 列一致，
   非 HCS 的 `team` 为空；冲绳和新潟 HCS 的区域归属符合数据合同。

同步逐用户事务执行；中途失败后重新 compare 并使用新的 digest 续做，
不能把已提交的部分修改当作整批回滚，也不能重新创建既有身份。

## 5. 已验证身份与历史覆盖

1. 通过新版 canonical projector 的现有身份绑定路径关联
   `identityVerified=true` 的 `chat_users` 身份。只按已验证邮箱匹配名单，
   保留既有稳定 subject，不根据姓名或消息内容猜测身份。
2. 在冻结期间用已更新 Job 执行受控普通刷新，以绑定身份并保存本次 execution
   的镜像、身份和终态。此时先检查新名单范围，历史覆盖验收仍在下一步。
3. 对新增名单中已有历史消息的人逐一核对 Firestore 会话及消息标识、时间范围，
   与 Monitor 的会话投影和问题事实比较。上一轮发现 4 人（DM 3、HCS 1），
   操作时重新核对，不能将人数作为固定过滤器。
4. 需要补齐时，仅选定已绑定的 `roster_id` 和明确 UTC 起止范围，先生成只读计划：

   ```bash
   MONITOR_ANALYTICS_START_AT='<CURRENT_JOB_ANALYTICS_START>' \
   PYTHONPATH=. .venv/bin/python -m app.jobs.backfill_roster_history \
     --credential-file '<APPROVED_CREDENTIAL_PATH>' \
     --roster-id '<VERIFIED_ROSTER_ID>' \
     --start '<UTC_START_ISO8601>' --end '<FIXED_UTC_END_ISO8601>'
   ```

   `MONITOR_ANALYTICS_START_AT` 使用当前 Job 的实际配置，不能为了扩大范围改早。
   多人使用重复的 `--roster-id`，一次限 1–20 个已验证用户、10,000 条编译事实和
   5 MB 原子请求。起止时间必须带时区，范围不得早于分析起点或超过当前时间。
   核对 `selectedUsers`、`source`/`missing` 中的问题、回答、会话和引用行数，
   以及目标范围和 `planDigest` 后，使用相同参数增加
   `--apply --expected-plan-digest '<planDigest>'`
   `--freeze-snapshot '<FREEZE_JSON>' --region us-central1`
   `--receipt-output '<NEW_RECEIPT_JSON>'`。冻结证据必须与实时状态一致。
5. 历史补齐只插入缺失的目标事实，遵守 publisher lease；不覆盖已有事实，
   不推进或回退 watermark。保留计划、写入回执和逐项读回结果。
   回执应为 `receiptType=monitor.roster-history.v1`、`coverageVerified=true`、
   `requiresNormalRefresh=true`，并包含 `planDigest`、`verificationDigest`、
   原发布状态和验证时间。历史使用记录没有对应遥测时保留“未测量”状态，不能
   凭已有 Firestore 消息推断完整交付、耗时等遥测指标。
6. 再运行新版普通刷新，发布包含完整名单和补齐历史的正式快照。
   核对 `published_run_id`、`scope_policy_version=summary_department_v2`、
   范围指纹、数据质量结果和新 execution 的 immutable image/运行身份。

普通增量刷新只读取近期更新的会话，不能凭一次成功证明新绑定用户的旧历史已完整。
暂无身份或活动的用户仍保留在名单；无法读取来源属于错误，不能显示成确定的零使用。

## 6. 候选 API、页面及定时刷新验收

1. 使用真实管理员登录候选页面；核对全体 MR / DM / HCS 的 KPI、用户列表、
   地图、趋势、News 和 CSV 在相同日期、区域、人群条件下对应一致。
   预计活动名单基线为 138 / 64 / 74；用户管理 162，用户分析 159。
2. 核对已有关联历史的用户能打开会话，新增但暂无记录的人仍可见；不要求上线后
   产生新的用户活动才能通过名单和旧历史验收。
3. 确认地图 46 个可见县全部归属正确、冲绳无图形但人数计入九州；新潟 HCS
   按 A 列归属，不因地图分类改变。切换三个标签后分母和区域值随人群同步变化。
4. 数据和候选验收完成后，仅恢复本次冻结前启用的规范 Scheduler；读回
   `ENABLED`、既有计划、时区、目标和身份，并验证一次自然定时 execution
   使用新 digest、成功发布、人数及历史没有回退。
5. 通过现有 `scripts/promote_candidate.sh` 的候选绑定验收及发布凭据校验后切换流量，
   再核对生产 revision、tag 和正流量分配。该脚本当前要求规范 Scheduler 在
   切流前为 `ENABLED`，因此实际顺序为：冻结 → 数据更新 → 候选验收 →
   恢复并验证定时刷新 → 流量切换。不得跳过或伪造其 activation、backfill、
   API、浏览器验收和 DTS 45 分钟/72 小时观察等凭据；所有证据必须针对新候选
   和镜像重新验证，旧候选凭据不能替代本次候选证据。

## 中断与交接

- 名单同步前失败：保留原名单及统计，按保存的镜像和触发器状态恢复；记录实际结果。
- 同步或补齐后失败：保持触发器暂停，保留已成功提交的修改，读回并修复差异后续做。
  不把不支持新部门的旧 Job 镜像恢复运行，也不删除真实历史来凑统计结果。
- 候选页面或发布凭据未通过：保留原 Web 流量，明确报告卡点；不要将 build、
  Job success、HTTP 200 或模拟浏览器测试当作线上完整验收。
- 交接时分别列出代码/commit/push、构建、候选、名单、身份/历史、新快照、
  Scheduler 和生产流量的实际状态，注明哪些尚未执行。
