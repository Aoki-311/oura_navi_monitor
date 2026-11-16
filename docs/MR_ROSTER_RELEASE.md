# Monitor 日常更新与用户名单发布

日常更新使用 [`scripts/routine_release.py`](../scripts/routine_release.py)。先让生产
Web 能读取当前及新版快照，再更新任务、同步名单并发布新版数据。本文件是操作说明，
不是已完成发布的凭据；代码、本地测试、构建、数据写入与真实页面验收分别记录。

本轮来源为 `260811_OurANavi開発・運営業務チェックリスト_最終版_20261117.xlsx`
中的 `OurA-Naviユーザー管理` 和 `DMエリア追加 支店別対象管轄エリア `。
工作簿可能由用户继续保存，每次计划均重新计算文件哈希，不能仅按文件名复用旧计划。
工作簿、用户身份明细及发布证据留在仓库外的受控目录，不提交 Git 或放入构建镜像。

## 数据与展示合同

| 来源 | 字段及处理 |
|---|---|
| A エリア | `area`；MR 按原值规范化，不根据勤務地改写 |
| B 勤務地 | `workplace`；沿用现有文本规范化 |
| C 社員名 / D 邮箱 | `name` / `email`；按规范化邮箱匹配既有名单 |
| E 役割 | `role`；社員MR、コントラクトMR、本部メンバー等，与 H 列不同 |
| F MR经历 | `mr_experience`；DM/HCS/ブロック保存，本社/管理员沿用 `-` |
| G 備考 | `department`；含 MR(ブロック)、MisW本社、セールスマーケ本社 |
| H 额外职务 | 忽略；不保存、不新增字段、标签或输入项 |
| I HCSチーム | `team`；仅 HCS 可填写和保存 |

本社/管理员的 `東京 + 虎ノ門` 沿用既有 `本社・虎ノ門` 地区身份。
2026-11-17 名单及保留当前启停状态的同步计划基线如下：

| 范围 | 人数 |
|---|---:|
| 管理名单 | 183 |
| 启用 / 停用 | 176 / 7 |
| 全体统计 | 150 |
| DM / HCS / ブロック | 64 / 75 / 11 |
| 用户地图及个人分析 | 172 |

保留原有 7 人停用，新增 13 人默认启用。本次同步不删除真实用户，不为凑人数强行
启用既有用户。全体统计仅含启用的三类 MR；启用的 DM本社、ヘルスケア本社和
セールスマーケ本社进入用户地图及个人分析，MisW本社和管理员仅保留在用户管理。
最终以新计划的 `resultingRosterCounts` / `resultingScopeCounts` 和写后读回为准。

全体サマリー提供 `全体 / DM / HCS / ブロック` 四个页签。全体比例按合并后的
分子、分母重算，不能相加各组百分比。四个页签共用エリア地理划分；冲绳没有图形，
用户仍计入九州及所属人群。勤務地为新潟的 4 名 HCS 按名单 A 列计入北海道東北，
新潟县图形归関東A。支店管辖细分不产生独立县级统计，也不把人数重复分摊到各县。

## 1. 固定发布输入并生成只读计划

1. 固定待发布的完整 Git SHA、最新工作簿哈希和本地验证结果。完成 commit/push，
   经本次构建授权后执行 Cloud Build；保存真实 build ID、终态、候选 revision、
   Ready 状态、前后端 build identity 及不可变 image digest。构建仅创建无流量候选。
2. 按脚本合同准备本次 `release.json`，使用受控的仓库外证据目录；不要复用旧候选
   或旧工作簿的 manifest、验收回执。实际参数以脚本 `--help` 和已验证输入为准。
3. 生成只读基线与 `routine-state.json`：

   ```bash
   "$PY" scripts/routine_release.py plan \
     --manifest '<RELEASE_JSON>' --evidence-dir '<EVIDENCE_DIR>' \
     --credential-file '<APPROVED_CREDENTIAL_PATH>'
   ```

`release.json` 使用脚本严格校验的字段：

- `protocol=monitor.routine-update.v1`，以及 `project`、`projectNumber`、`region`、
  `service`、`job`。
- 本次 `gitSha`、`buildId`、`revision`、不可变 `image` 和原 `previousRevision`。
- `webServiceAccount`、`jobServiceAccount`、两个实际触发器对应的
  `schedulerAccounts`、既有 `legacyTransferResource`。
- 已审查依赖证据的绝对路径 `legacyDependencyEvidence`，及其文件
  `legacyDependencyEvidenceSha256`；本轮证据为
  `legacy-dts-canonical-dependency-readback.json`，留在仓库外受控目录。
- 工作簿绝对路径 `workbook`、`workbookSha256`、审计操作者 `actor`；`actor` 为
  实际固定凭据的 service account 邮箱，与浏览器验收的人类管理员身份分开。
- `expectedRoster` 包含 `total`、`active`、`inactive`、`deleted`、`global`、
  `userMap`；本轮分别为 183、176、7、0、150、172。

触发器键使用当前规范 `oura-navi-monitor-refresh-three-hour` 与已暂停旧资源
`oura-navi-monitor-refresh-quarter-hour`。名称里的 three-hour 不代表实际周期；
现有规范计划为 `5 * * * *`、`Asia/Tokyo`。脚本要求规范触发器启用、旧触发器暂停
的既有基线；遇到不同状态先核对原因，不能为了满足计划而启用未知或已暂停资源。

`PY` 指向本次已验证的 Python 环境。本地 GCP 命令使用用户指定的
`/Users/lee/Downloads/VScode/credentials/lcs-developer-483404-9c869eeb70bd.json`，
通过单条命令参数传入，不改变 Cloud Run、Job、Cloud Build 或 Scheduler 的运行身份。
目标为项目 `lcs-developer-483404`、区域 `us-central1`、Firestore 数据库
`lcs-user-data`、BigQuery dataset `oura_navi_monitor`（`US`）。

计划保存当前流量、候选身份、既有 Refresh Job 配置、所有实际触发器及原始启停
状态、在途 executions，以及 Chat/News 两条发布链的快照和 publisher leases。
执行前再次读取这些状态；旧计划不是对当前云端状态的保证。两条发布链由同一个
`oura-navi-monitor-refresh` Job 执行，不是两个独立 Job。

## 2. 按阶段执行日常更新

每一步默认只输出计划。先核对输出，再以同一状态文件和参数添加
`--apply --confirm '<EXACT_CONFIRMATION_FROM_PLAN>'` 执行；确认串使用脚本本次
输出值，不能自造或复用其他阶段的值。例如冻结阶段的只读命令为：

```bash
"$PY" scripts/routine_release.py step \
  --state '<EVIDENCE_DIR>/routine-state.json' --stage freeze \
  --acceptance-receipt '<CANDIDATE_V2_ACCEPTANCE_JSON>' \
  --credential-file '<APPROVED_CREDENTIAL_PATH>'
```

| 阶段 | 操作及完成条件 |
|---|---|
| `freeze` | 提供候选 v2 真实验收回执；暂停实际启用的触发器，保留原始状态，等待 Refresh Job、相关写入及 Chat/News 两个 publisher leases 全部退出 |
| `promote-reader` | 提供 `--acceptance-receipt` 的候选 v2 真实 API/浏览器验收，先切入可读取 v2/v3 的 Web；此时数据仍为原快照 |
| `update-job` | 提供生产 v2 验收回执；仅把现有 Refresh Job 的 image 更新为本次不可变 digest，逐字段核对其他配置不变 |
| `sync-roster` | 冻结后重新生成名单差异；核对本次 `syncDigest`，执行时以 `--expected-sync-digest` 传入原值，再核对逐项读回 |
| `refresh` | 运行更新后的 Chat/News 刷新，验证 execution、镜像、身份、发布状态及 v3 快照范围 |
| `verify-v3` | 提供生产 v3 真实 API/浏览器验收回执，确认新名单、四个页签及相关明细一致 |
| `restore-schedulers` | 恢复冻结前各 Scheduler 的原始状态；原先暂停的仍暂停，核对计划、时区、目标和运行身份 |
| `complete` | 以 `--natural-evidence` 选择一次自然定时 execution；脚本实时读取 execution、发布状态、日志及审计证明，再完成校验 |

`freeze` 和 `promote-reader` 使用 `candidate-v2` 回执，`update-job` 使用
`production-v2`，`verify-v3` 使用 `production-v3`，均由 `--acceptance-receipt`
传入。回执格式为 `receiptType=monitor.routine-acceptance.v1`，并包含：

- 精确 `project`、`region`、`service`、`revision`、`image`、`gitSha`、`buildId`、
  对应 `stage`，以及实时 candidate tag URL 或生产 URL 的 `origin`。
- 60 分钟内的 `capturedAt`、`verifiedBy`，以及均为 true 的
  `authenticatedApiAcceptance`、`loggedInBrowserAcceptance`、`readOnlyAcceptance`。
- `authenticatedIdentity` 的 `source=iap`、`verified=true`，及真正登录且属于
  管理员 allowlist 的人类邮箱 `email`，不能使用 service account 代替。
- `apiEvidence` 和 `browserEvidence` 各自的绝对 `path` 与实际文件 `sha256`。
- `endpointStatus` 中 `overview`、`regions`、`users`、`userDetail`、`news`、
  `newsCsv`、`management` 均为 200；`newsCsv` 仅指只读 GET News CSV。
- 与当前线上发布完全一致的 `chatPublishedRunId`、`newsPublishedRunId`。

验收必须来自真实接口和登录页面。模拟 API 测试、HTTP 200、旧截图或手写 PASS
字样不能替代验收。计划绑定 controller 文件哈希；本地导入和运行依赖必须与候选
镜像的 Git SHA 对应，不能计划后替换控制器或用未发布的本地代码执行同步。

中断阶段保存为 pending；读回实际状态后，使用同一阶段、确认串和 `--recover`
续做。`refresh` 提交异步 execution 后会暂停等待，不能再次盲目提交；终态读回后
继续该阶段。只有提交结果不明且已独立核实具体 execution 时，才在恢复命令中
使用 `--execution-name` 指定它。

冻结同时覆盖 Chat 和 News，暂停触发器不等于在途任务或租约已结束。新 v3 快照发布
前，生产 reader 必须已能读取 v3；不能先写新快照再让仅支持 v1/v2 的旧 reader 服务。
旧快照仍按各自冻结的策略验证，不能用旧策略回执接受新部门。

Job 更新保留 service account、command/args、环境变量、数据库、来源、分析起点、
任务数、重试和超时。这里不运行 `bootstrap_gcp.sh`，不重建 IAM、日志源、Scheduler
或数据集，不手动修改 watermark。除 image 及平台元数据外的意外差异应停止该阶段。

本次实际云端核对发现旧 DTS 配置 `6a1d2e24-0000-22fe-b534-582429aee120` 仍启用，
其观察到的运行状态为 `FAILED`，并非已停用。查询仅写入四张旧表；读取 24 个实际
BigQuery 对象的元数据后，已确认这些写入目标与当前 canonical API、增量 Chat/News
发布链的完整依赖范围没有交集。日常流程保留现有 DTS 配置，仍须以精确查询和实时
依赖证明核验该分离关系，不能仅凭 FAILED、名称或旧审计结论放行。原始 SQL 与云端
证据保留在受控证据目录，不复制到 Git。

manifest 绑定审查后的依赖证据路径及哈希。每次变更前，脚本重新读取完整依赖范围
内对象的类型、定义哈希和字段，核对已审查的四表 DTS 查询哈希，并保留整个 transfer
配置合同，包括当前启用状态。若数据集中仍有写入任务则继续等待；不会一概忽略
DTS 写入，也不会在本流程中修改 DTS。

这种分离证明不代表初次迁移已全部完成，也不授权停用或修改 DTS。本流程不调用
`rebuild_history`，因为它仍读取旧 `monitor_answer_events`，不在上述独立增量链中。

## 3. 名单同步与读回

冻结后必须重新读取线上状态，不能复用构建前的 `syncDigest`。底层同步工具仍可用于
独立只读比对；日常发布的写入由 `sync-roster` 阶段统一记录：

```bash
PYTHONPATH=. "$PY" scripts/import_monitor_users.py '<LATEST_WORKBOOK_XLSX>' \
  --sync --compare-current --credential-file '<APPROVED_CREDENTIAL_PATH>'
```

1. 对照工作簿哈希检查 `createUsers`、`updateUsers`、`unchangedUsers`、
   `outsideWorkbookUsers`、字段变化及最终范围。使用相同文件、参数和新生成的
   `syncDigest` 执行，任何输入或线上记录变化都要重新生成计划。
2. 确认 `readbackMatched=true`，再次 compare 得到新增 0、更新 0。逐字段核对
   工作簿中的用户，保留原 `roster_id`、身份绑定、标签、启停状态和创建时间。
   不在工作簿中的用户保持原状；已删除身份跳过同步，旧 Excel 不能将其恢复。
3. 确认原有 7 人仍停用、没有真实用户删除；H 列未进入存储，75 名 HCS 的 `team`
   与规范化后的 I 列一致，非 HCS 的 `team` 为空。三类 MR 保留 MR 经历。
4. 同步按用户事务执行。中途失败后重新 compare 并使用新 digest 续做，不能把已
   提交的部分修改当作整批回滚，也不能重新创建既有身份。

## 4. 真实数据与页面验收

1. 候选 v2 验收确认新代码能读当前已发布快照；切换 reader 后再次确认生产 v2。
   这两个验收均在新名单和 v3 快照发布前完成。浏览器使用真实管理员登录，记录
   revision/build identity、数据版本及 API 与页面结果，不用模拟接口代替。
2. v3 发布后核对名单 183、启用 176、停用 7；全体 150、DM 64、HCS 75、
   ブロック 11；用户地图及个人分析 172。若实际源状态变化，报告具体差异，
   不强行修改人员状态以满足基线。
3. 在相同日期、地域、人群条件下，核对四个页签的 KPI、趋势、地图、用户列表、
   CSV，以及 News 和个人历史的对应关系。缺失数据或无法读取来源不能显示为
   确定的零。已有关联历史的用户应能打开会话，暂无活动的用户仍保留在名单。
4. 确认 46 个可见县归属、冲绳人数及新潟 HCS 的 A 列归属。确认 H 列没有新增
   标签，原有 7 名停用用户可在管理页面查看。不能为测试删除入口而删除真实用户。
5. 恢复各触发器原始状态后，观察实际自然定时执行，核对新镜像、运行身份、成功
   发布及人数未回退。手动 Job execution 不替代自然执行证明。`complete` 的
   `--natural-evidence` 文件仅含 `{"executionName":"oura-navi-monitor-refresh-..."}`；
   脚本自行读取实时 execution、发布状态、日志和审计并验证关联，不接受调用方
   自制的成功回执作为自然执行证明。

普通增量刷新仅读取近期更新的会话，一次成功不能证明新绑定用户的旧历史全部覆盖。
有历史缺口时按下方受控补齐流程处理；暂无已验证身份或活动不等于导入失败。

## 5. 管理页面删除的含义

停用用户继续出现在默认管理名单中；删除操作归档 Monitor 名单记录，保存删除时间、
操作者、身份约束、历史日志和审计，默认列表不再显示。它不撤销 OurA-Navi 登录权限。
本轮名单同步不删除真实用户，也不把现有 7 名停用用户视作待删除对象。

删除确认明确显示姓名和邮箱，以当前更新时间及范围策略版本防止覆盖并发修改。
前端读回归档标记与默认列表，均一致后才显示成功；冲突先刷新状态，网络结果不明时
只读确认，避免重复删除。普通编辑、身份绑定及 Excel 同步不能恢复已删除身份。
分析页在下一次成功发布快照后排除该用户，不承诺删除后立即改变现有统计。

## 初次迁移及历史补齐工具保留

`register_monitor_v2_revision.py`、`cutover_refresh_scheduler.sh` 和
`promote_candidate.sh` 保留初次迁移用途与原有校验合同；不再作为每次日常更新的
首入口。它们要求的 activation、backfill、API、浏览器与 DTS 观察凭据不能用
routine 状态文件冒充，旧 v2 验收也不能证明本次 v3 数据已通过。
无 `--sync` 的名单 bootstrap 模式仍禁止覆盖既有不同记录。

仅在真实来源核对确认历史缺口，并授权明确身份与时间范围后使用
`app.jobs.backfill_roster_history`。保留原工具的冻结证据、已验证 `roster_id`、
带时区的起止时间、当前分析起点、计划 digest、批次限制及写后覆盖验证要求。
routine 冻结回执不能未经适配就当作旧补齐工具的冻结 JSON。补齐仅插入经核实的缺失
事实，不覆盖已有事实、不推进或回退 watermark；无法证实的遥测保持“未测量”。
按原合同完成补齐后再正常刷新，并重新验证最终快照及历史覆盖。

其他初次切换与 News 合同见 [实施与切换清单](IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md)
及 [News 发布步骤](NEWS_USAGE_RELEASE.md)。

## 中断与交接

失败后保留阶段 intent、锁及状态文件，读取实际结果后续做，不把部分成功说成整批
回滚。发布 v3 后不能自动退回不兼容的旧 reader，也不能恢复不支持新部门的旧 Job。
Scheduler 按保存的原始状态恢复，不将所有触发器一律启用。

交接分别列出代码/commit/push、Build、reader 流量、Refresh Job、名单、身份/历史、
快照、真实验收和 Scheduler 的实际状态，注明未执行项及对应证据。
