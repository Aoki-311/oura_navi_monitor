# OurA Navi Monitor 最终产品与数据规范

更新日：2026-08-30

## 0. 文档地位与当前结论

本文件是 Monitor 产品口径、数据责任和切换顺序的唯一规范。产品不建立并行的 `v2`、
`v3`、`legacy`、`shadow` 或 `backup` dashboard；内部 BigQuery reader 仍使用版本化函数完成
不中断的 additive 切换。当前代码只读带 `published_run_id` 的 v2 函数，旧两参数函数只是
旧 revision 排空期间的兼容 wrapper，不构成第二套产品或语义 owner。LCS 已公开的
`/v3/ask/stream` 是上游业务路由，不属于 Monitor 页面版本命名。

本文件定义目标产品合同，不记录未经本轮重新读取的“当前线上状态”。代码、Git、build、
candidate、IAP 浏览器、业务验收、数据补齐、Scheduler、DTS 和流量是互不替代的证据层；
任何一层未通过时，都不能把本文件中的目标状态写成“已经生产收口”。实际切换必须由
`IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md` 和 `THREE_HOUR_RECOVERY_RUNBOOK.md` 的
不可变 receipt 与云端 readback 逐项证明。

## 1. 最终目标

OurA Navi Monitor 是 LCS RAG APP 的用户数据分析平台，不是工程告警台。它必须让
非技术人员直接看懂：

1. 哪些员工在使用，采用率、回访率和活性如何；
2. 用户在什么时间、设备和模式下使用；
3. 用户在问什么类型、产品和任务；
4. 回答是否真正完整交付，耗时如何；
5. 地区、角色和个人之间有什么差异；
6. 哪段会话可以解释某个用户的使用轨迹。

数据缺失、过期或单模块失败必须局部显示 `-`、`未計測` 或模块错误。任何一个
请求、图表、分类或会话异常都不能让三个页面整体空白。

## 2. 系统性根因与唯一修复

| 根因 | 唯一修复 |
| --- | --- |
| 新页面读取空的 canonical facts，而 4,176 行旧表仍在另一条链 | 一次性历史编译把 Firestore、旧审计表和 retained raw telemetry 合并到同一事实表 |
| 页面依赖一个 mega response，任一字段失败就整页失败 | overview、regions、users、user detail、conversations 独立 API 与独立模块状态 |
| 全局鲜度状态被当成页面开关 | 鲜度只做元数据；历史、部分和未测量数据仍显示 |
| 缺字段、无记录和真实 0 被 adapter 混为 0 | 封闭 Pydantic 合同；缺失是 `null/未測定`，日期轴上的无事件日才是 0 |
| 旧请求、导出或导航完成后覆盖新页面 | `frontend/api/client.js` 和 `frontend/app.js` 统一 AbortController 生命周期 |
| 用户、标签、身份和地区在多处各自判断 | 用户管理 service/repository 是唯一写 owner；名单 `エリア` 是地区权威 |
| 80/83 人被一次性堆成长表，筛选状态又随导航丢失 | 页面 URL 是筛选/排序/分页唯一状态；桌面和手机分别使用受控分页 |
| 旧首页用方块拼成“地图”，无法表达日本地区 | 导入一份有来源的日本都道府县 SVG；只把名单 `エリア` 投影为呈现区域 |
| 历史未测量字段被画成 0 或“分类不明” | 每个可选分析字段都携带 measured/partial/not_measured/no_usage 状态与覆盖数量 |
| 历史问题主题被复制成假依頼任务 | 历史 `analytics_tasks` 保持空；新 producer 必须提供真实任务，页面按来源显示计测状态 |
| 空设备/模式被统计为用户主动选择的“unknown” | 空值和 `unknown` 不进入分布，只进入计测覆盖分母 |
| 停用用户仍能从旧书签打开个人分析/会话 | `analysis_scopes` 的 active membership 同时约束详情和 trace，前端回到选择页说明原因 |
| 三页各自堆样式，缺少商务 BI 层级 | `frontend/styles.css` 和图表组件是唯一视觉系统，统一密度、色板、状态和响应式 |
| 历史编译逐会话读取 Firestore，8 分钟仍卡住 | 一个 root stream、一个 conversations collection-group stream、一个 messages collection-group stream |
| 坏会话没有时间时用当前时间补齐 | `ProjectionDataError` 明确排除并计数，绝不制造使用日期 |
| 旧成功率语义错误，且历史只明确记录失败会制造偏置 0% | 旧 `answer_success_flag` 不迁移；只有完整、可比较测量 profile 进入成功率分母 |
| `user_daily`、snapshot 和 mega view 会互相漂移 | 事实表 + `pipeline_state.published_run_id` 指针 + 同 run 的 `user_scope` projection + run-bound v2 reader |
| 晚到事件按到达时间取源、按业务时间写分区，去重/补充/质量范围互相错开 | 每个 run 以实际事件 ID 和有效业务分区为唯一处理集合，重跑仍只有一份事实 |
| 坏记录只有总数，水位前进后无法知道去哪了 | `pipeline_run_event_manifest` + `pipeline_event_issues` 保存哈希化逐事件 disposition、原因和解决状态 |
| 质量失败把诊断和事实一起回滚 | 事实事务回滚后，独立持久化本次质量结果和 typed failed run；旧成功数据继续可读 |
| 页面只看上次成功，最新失败被隐藏 | API 同时返回 published run 与 latest run，三页共享 banner 说明“最新失败、当前显示上次成功” |
| 15 分钟 Scheduler、旧 DTS 和手工补数可同时写 | 冻结 execution/BigQuery DML、固定目标补数、不可变 receipt、三次 Scheduler provenance 后才暂停 DTS |
| 发布身份被误当成 source build 前置条件 | Monitor candidate 保持当前 runtime identity；Refresh writer 与 Scheduler invoker 只在后续数据激活时分离并验证 |

## 3. 唯一责任模块

| 责任 | 唯一 owner | 禁止的冲突路径 |
| --- | --- | --- |
| Summary/用户分析范围 | `app/domain/analysis_scopes.py` | SQL/前端按邮箱、标签或人数再排除 |
| 问题主题运行时合同 | LCS producer + `app/domain/question_categories.py` 校验 | Monitor 关键词分类、别名猜测 |
| 用户想完成的任务 | `analytics_tasks` + `app/domain/analytics_tasks.py` | 把问题主题与依頼任务混成一个分类 |
| 历史旧分类转换 | `migrate_legacy_question_category()` | 运行时接受旧枚举 |
| 历史 Firestore 读取 | `FirestoreChatReader` | 每个会话再查询一次 messages |
| 会话/引用投影 | `app/jobs/project_firestore.py` | 用当前时间、正文关键词或第二份地区表补值 |
| 一次性历史合并 | `app/jobs/rebuild_history.py` + `sql/merge_history.sql` | 长期双读旧表 |
| 增量发布与水位 | `app/jobs/refresh_analytics.py` | DTS 全量重建、两个同时启用或永久并存的 scheduler |
| 源事件逐项去向 | `pipeline_run_event_manifest` + `pipeline_event_issues` | 只有批次总数、丢弃后无重放线索 |
| 质量账与最新运行状态 | `pipeline_quality_events` + `pipeline_runs` | 失败时回滚诊断、页面只读旧成功状态 |
| 三小时控制面 | `app/refresh_policy.py` + cutover/backfill/receipt scripts | YAML、env、Scheduler、告警各写一套时间常量 |
| 页面事实语义 | BigQuery `dashboard_events_v2` / `dashboard_user_list_v2`（精确绑定 published run） | overview/detail 各自复制公式或运行时读取实时 Firestore 名单 |
| 页面组装 | `app/services/analytics_service.py` | JavaScript 重算 KPI |
| 完整交付率的可比较样本 | `AnalyticsService` 的封闭 measurement profile | 把仅记录失败的历史子集当作代表性分母 |
| 前端请求生命周期 | `frontend/api/client.js` + page controller | 页面私有 fetch、旧请求覆盖 |
| 名单/标签/审计 | `user_management.py` + `user_directory.py` transaction | 前端直写数据库、两套标签状态 |
| 会话正文 | `conversation_history.py` 分页读取 LCS Firestore | 把正文复制进 BigQuery |

## 4. 用户名单、地区与范围

首次导入权威是 `../OurA-Navi_userlist.xlsx`。2026-08-25 只读核对结果：83 行；
`MR=61`、`本社（ヘルスケア）=8`、`本社（DM）=11`、`システム管理者=3`。

| 名单属性 | 全体 Summary | 用户分析/地图/详细 | 用户管理 |
| --- | ---: | ---: | ---: |
| 有效，角色为 `本社MR` | 是 | 是 | 是 |
| 有效，角色为 `コントラクトMR` | 是 | 是 | 是 |
| 有效，其他非管理员角色 | 否 | 是 | 是 |
| 管理员或停用用户 | 否 | 否 | 是 |

全体 Summary 只看有效名单中角色精确为 `本社MR` 或 `コントラクトMR` 的人员。部门继续
约束用户分析/地图的非管理员资格，但不能单独让一个人进入 Summary。人数必须每次从当前
名单动态计算，不写死历史的 69/80/83。Monitor 分析标签只用于展示和筛选，不能改变范围
或 IAP 权限。

地区直接使用 Excel `エリア`。`首都圏A` 保持原样；只有本社且勤務地为虎ノ門时
单独显示 `本社・虎ノ門`。其他用户沿用其 `エリア` 与 `勤務地`，不创建地点字典。

## 5. 页面最终结构

### 5.1 全体サマリー

| 模块 | 范围 | 内容 | 呈现 |
| --- | ---: | --- | --- |
| 核心 KPI | Summary 角色 | 活跃人数、利用率、回访率、人均提问、回答成功率、P95 | KPI 卡 |
| 利用环境/模式 | Summary 角色 | 时段、设备、社内/Web | 柱形 + 圆环 |
| 利用推移/依頼类型 | Summary 角色 | 每日活跃/提问、用户想完成的任务 | 双轴趋势 + 横条 |
| 活性度分布 | Summary 角色 | 高、中、低、休眠；地区/角色比较 | 圆环 + 100% 堆叠 |
| 用户一览 | Summary 角色 | 姓名、邮箱、エリア、最后使用、7 日使用日、7 日消息、成功率、活性度 | 表格 |
| 日本使用地图 | Summary 角色 | 活跃、提问、利用率、回访率、地区排名 | SVG 热力图 + 横条 |
| 产品需求 | Summary 角色 | 产品 Top 10、产品 × 依頼任务 | 横条 + 矩阵热力图 |

地区点击只形成一个可关闭的地区筛选；不增加复杂全局筛选栏。
地图默认显示 100%，允许按 25% 逐级放大到 200%，放大后可拖动查看局部并一键恢复
全体显示；缩放只改变前端视野，不改变地区统计或筛选条件。

### 5.2 ユーザー分析

1. 个人摘要：区域、地点、部门、MR 资历、最后使用、活跃天数、提问数、日均提问、
   完整交付率、同地区/同角色比较；
2. 个人趋势：提问柱形 + 完整交付率折线；
3. 用户需求画像：产品、问题主题、依頼任务、模式、设备；
4. 会话旅程：现有会话列表 + 消息列表双栏，不增加字段；
5. 标签 Chip 与“前往用户管理编辑”。

个人分析事实和会话正文是两个请求。会话读取失败不得抹掉个人摘要，个人事实失败
也不得让会话区伪装成空会话。

### 5.3 ユーザー管理

用户与标签为两个 `role=tab` 子页。可管理姓名、邮箱、エリア、勤務地、角色、部门、
MR 资历、标签和有效状态。エリア为封闭名单值；本社自动约束虎ノ門。标签可新增、
改名、改固定色、停用和删除；正在被使用的标签不可删除。

所有编辑携带页面所见 `updated_at`。后台 transaction 再检查版本；并发冲突返回
`update_conflict`，抽屉保持打开并显示就地错误，不覆盖他人刚保存的值。停用标签
仍显示在已分配用户上，但不能新分配。

## 6. 指标白话口径

| 指标 | 白话定义 |
| --- | --- |
| 活跃人数 | 选择期间至少提交过一次有效问题的人数 |
| 利用率 | 活跃人数 ÷ 当前范围内有效名单人数 |
| 回访率 | 在至少两个不同日期使用的人数 ÷ 活跃人数；单日范围显示 `-` |
| 人均提问 | 有效问题数 ÷ 活跃人数 |
| 回答成功率 | 可测量回答中满足完整交付的比例，同时显示“已测量/全部问题” |
| P95 | 有耗时记录的回答中，95% 不超过的耗时，同时显示测量覆盖数量 |

回答成功率就是完整交付率。新数据只有同时满足终态 final、运行 completed、无遗漏/
部分 demand、无 system/writer/assistant error，并确认消息写回，才算成功。只有
`runtime_truth_full` 与 `complete_delivery_full` 这种对成功和失败采用同一完整标准的
profile 才能进入分母。旧历史只明确留下失败结果时，失败事实继续保留，但 KPI 显示
`履歴未計測`，不能用失败单边样本制造 0%。所有已发布比例仍必须显示测量覆盖数量。

活性度唯一口径只看最近 14 个自然日里“有过有效提问的不同日期数”：6 日以上为高、
3–5 日为中、1–2 日为低、0 日为休眠。提问次数不能冒充活跃天数。

## 7. 问题主题与依頼任务

本轮只修改 Monitor，不替换 LCS 已有运行时分类合同。Monitor 不读问题正文做关键词
归类，也不新建另一套“更聪明”的分类。首页回答“用户想做什么”，使用可多选的
`analytics_tasks`，页面统一显示为 `質問種類`；个人页才把
“问的是什么主题”和“想完成什么任务”并列展示。

现有问题主题封闭枚举继续为：

运行时封闭枚举：

| Key | 页面日文 |
| --- | --- |
| product_information | 製品情報・仕様 |
| price_product_code | 価格・製品コード |
| comparison_fit_selection | 比較・適合・選定 |
| usage_procedure | 使用方法・手順 |
| troubleshooting_safety | トラブル・安全対応 |
| sales_proposal | 営業活動・提案作成 |
| institution_gpo_market | 医療機関・GPO・市場情報 |
| document_search | 資料・文書を探す |
| other_general | その他・一般質問 |
| unclassified | 判定不能 |

一次性旧 schema 只做精确枚举转换：

| 旧值 | 新值 |
| --- | --- |
| product_explanation | product_information |
| product_price | price_product_code |
| troubleshooting | troubleshooting_safety |
| sales_approach | sales_proposal |
| hospital_gpo | institution_gpo_market |
| topic_ideation | unclassified |

`topic_ideation` 过去是未知问题的默认值，不允许升级成具体业务结论。运行时 producer
继续拒绝所有旧值；不存在关键词特判或隐藏别名。

依頼任务封闭枚举为：

| Key | 页面日文 |
| --- | --- |
| fact_lookup | 情報確認 |
| explanation | 説明依頼 |
| comparison_selection | 比較・選定 |
| procedure_guidance | 手順確認 |
| troubleshooting | 問題解決 |
| content_creation | 資料・文面作成 |
| source_retrieval | 資料検索 |
| market_research | 市場・施設調査 |
| other | その他 |
| unclassified | 判定不能 |

一条问题可以有多个依頼任务，所以产品矩阵按 `产品 × 依頼任务` 统计，不能拿单选
问题主题替代。

历史来源没有可靠依頼任务时，`analytics_tasks` 必须为空并显示 `履歴未計測`，不能写入
`unclassified` 冒充一次真实判定。新 canonical producer 若缺少任务，数据质量门直接失败。
模式和设备同样不把空值或 `unknown` 画进分布：没有使用记录显示无数据；全部缺失显示
履历未计测；部分有值则保留真实图表并同时显示覆盖数量。

## 8. 唯一数据链与正式对象

```text
retained Cloud Run request/stdout/stderr + LCS Firestore + retired audit table
                         ↓ one-time history compiler
canonical BigQuery facts + run-versioned user_scope ← bounded incremental canonical events
                         ↓ pipeline_state pointer + dashboard_events_v2 / dashboard_user_list_v2
FastAPI page APIs + independent Firestore conversation API
                         ↓
three dashboard pages with module-local state
```

保留原始来源，不做行删除：

- `run_googleapis_com_requests`
- `run_googleapis_com_stdout`
- `run_googleapis_com_stderr`（只用于可取得的历史 runtime truth）

正式事实：`http_request_events`、`question_events`、`answer_events`、
`answer_action_events`、`demand_events`、`citation_events`、`conversation_events`、
`user_scope`。发布控制与证据：`pipeline_runs`、`pipeline_state`、
`pipeline_quality_events`、`pipeline_run_event_manifest`、`pipeline_event_issues`。

当前 Monitor revision 的正式语义入口只有：

- `dashboard_events_v2(p_start_date, p_end_date, p_published_run_id)`
- `dashboard_user_list_v2(p_history_start, p_today, p_published_run_id)`

`dashboard_events(p_start_date, p_end_date)` 与
`dashboard_user_list(p_history_start, p_today)` 在混合 revision 窗口继续作为旧 reader 的
兼容 wrapper。wrapper 保持旧 revision 自洽的名单语义，但新代码不得调用，也不得把它当作
失败 fallback。只有旧 reader 正流量为零、最长请求 drain 完成、依赖 inventory 为零且规定的
观察门通过后，才可在独立授权步骤中退役；additive schema/routine 发布不得提前删除。

运行代码不存在 `user_daily`、`dashboard_overview`、`dashboard_user_detail` 或独立于
`pipeline_state` + run-versioned `user_scope` 的第二个 snapshot owner；这些旧对象在依赖、
流量和观察期完成前仍物理保留，但不再自动发布或作为 fallback。
`monitor_answer_events` 是一次性输入，不是页面 fallback；历史成功标志、raw payload、
问题正文和邮箱均不迁移。历史 apply 和精确验证成功前不得删除它。

## 9. 历史只读证据快照（不得当作当前状态）

以下数字是 2026-08-25 至 2026-08-29 期间的历史只读快照，只用于说明旧数据没有被删除
以及当时缺口如何形成。它们在本轮未用安全凭据重新读取，不能用来证明今天的线上状态：

- `question_events=3,331`、`answer_events=3,215`，均无 event ID 重复，覆盖 3/16–8/25；
- 当时 `dashboard_user_list` 返回 80 人，73 人有历史，近 7 日 29 人活跃、104 条消息；
- 当时 `user_scope=83`，旧范围合同曾返回 69/80/83；该人数不再是新 Summary 角色合同；
- `history_rebuild` 水位确认串已记录到 `pipeline_state`，证明 8/25 的历史 apply 成功；
- 116 条旧回答只由 111 条明确流式失败和 5 条明确 partial/omitted 构成，不是代表性
  成功率样本；修复后的 API 因此显示 `履歴未計測`，P95 仍使用可证明的耗时。

同日重新运行当前代码的 history plan，58.6 秒完成：

- 83 个 chat roots、2,205 个会话、7,291 条消息；
- 旧 `monitor_answer_events` 4,176 行去重为 3,441 个唯一请求/trace；
- 计划结果 3,346 个问题、3,230 个回答、1,557 个会话、18,418 条引用；
- 问题来源为 Firestore 2,937 + legacy-only 409；排除仍为 empty 37、out-of-scope 375、
  roster 外身份 312；unmatched user 6；`issueCount=0`；
- 新确认串为
  `lcs-developer-483404.oura_navi_monitor:2026-03-16:2026-08-26:3346:3230:0`。

因此过去数据已经在 canonical 显示，但 8/26 的 plan 比已发布事实多 15 个问题和
15 个回答；本轮没有 `--apply`，这部分差额仍待另行授权追平。`published` 水位虽更新到
8/26 09:57 UTC，事实最大日期仍是 8/25；在 LCS 新统一事件 revision 验收前，不能把
“刷新任务跑过”解释为“所有新问题已被采集”。

旧候选曾连接真实 BigQuery/Firestore 做 Chromium 验证：首页、地图、用户详细和用户管理
可打开。该快照早于本轮 Summary 角色合同，既不是当前 IAP 登录验收，也不是本轮候选或
线上业务验收。

2026-08-29 additive 修复后再次回读：`pipeline_state` / `pipeline_runs` 的新增字段、
`pipeline_event_issues`、`pipeline_run_event_manifest`、`pipeline_quality_events`、两张
source view 和两个同名 table-valued function 均已存在；只读合同验证全部通过。当前事实
为 `question_events=3,334`、`answer_events=3,218`，published 水位仍为
2026-08-27 00:57:05 UTC，lease 已释放。两个函数不只检查对象名，还在 64 MiB 查询硬
上限内真实执行、返回样本并核对后端所需字段。换句话说，旧历史没有被删除，schema 读取故障
已经解除，但两天/当前缺口尚未补齐。

2026-08-29 的旧控制面快照曾显示 15 分钟 Scheduler 启用、三小时 Scheduler 未创建，且
Web 与 Refresh Job digest 不一致。本轮必须重新 inventory 后才能决定下一步；旧快照不能
证明该状态今天仍成立。没有新的 build/candidate、同 digest Job、补数、登录验收、三次
定时执行、DTS 暂停观察和流量 readback 时，生产切换继续保持 STOP。

## 10. 正式 API

```text
GET /api/analytics/overview
GET /api/analytics/regions
GET /api/analytics/overview/users
GET /api/analytics/users
GET /api/analytics/users/{roster_id}

GET /api/trace/conversations
GET /api/trace/messages

GET /api/admin/metadata
GET/POST/PATCH /api/admin/users...
GET/POST/PATCH/DELETE /api/admin/labels...
POST/GET /api/export/jobs...
```

静态页面资源和全部 `/api/` 响应使用 `no-store`，前端 API 请求也显式绕过 HTTP 缓存，
避免 revision 或 schema 恢复后浏览器继续组合旧 HTML、旧 JSON 与新 JS。
生产身份只接受 IAP 注入邮箱并命中三名管理员 allowlist；这次 Monitor 分析升级不
新增任何 IAP key、页面 secret 或测试专用权限。

## 11. 唯一切换顺序

不建立 backup、shadow 或长期 fallback。具体命令和 STOP 条件以
`THREE_HOUR_RECOVERY_RUNBOOK.md` 为准：

1. 最后一次本地修改后完成全量回归、JS 合同、脚本/YAML/SQL 检查和 E2E；
2. 只读 inventory，固定旧 15 分钟 Scheduler、旧 DTS、raw、水位、revision 和 image；
3. 先准备能兼容 pre-contract/`monitor.v2` 的 Monitor reader、additive schema 和 0% 候选镜像；
4. 更新 Refresh Job 前先冻结旧 15 分钟 Scheduler；再部署 Job、创建暂停的新 Scheduler，并复核两者都暂停；
5. 验收 LCS 0% candidate：六条业务路由逐条验证 revision+trace+span、服务器持久化和
   正文保留；四条 debug 路由只进入 debug 分类；
6. 独立授权 LCS 流量后，用同一个增量 owner 以最长 24 小时窗口把缺失两天追到冻结当前水位；
7. 新 revision 的精确一请求一事件合同、`http_trace_contract_unavailable=0`、水位、两天趋势
   和独立分析轴通过后，只启用新 3 小时 Scheduler；
8. 新 Scheduler 完成 3 次不同 execution ID、不同窗口且有 Scheduler Attempt 佐证的真实定时执行；
9. 通过至少 30 天 jobs/Data Access/外部 owner 依赖门后只暂停旧 DTS 自动调度；
10. 暂停后完成 45 分钟和 72 小时观察，保留 transfer config、旧表和 raw 表；
11. 完成 Monitor 0% candidate 的 IAP/业务验收并显式切换其流量；LCS/Monitor 两个流量门
    互不代替。

任何一步失败都修复这一个 owner，不恢复旧 API、旧表页面读取或第二套状态。

## 12. 完成标准

只有以下全部具备真实证据才算完成：本地最终代码验证；旧路径关闭；历史/补数 manifest
与 facts 对账；增量水位；Monitor 构建/候选；IAP 登录；三页业务验收；LCS 新 revision
真实事件；新旧连续性；明确流量；三次正式调度；旧 DTS 暂停和 45 分钟/72 小时观察。
旧派生对象删除不属于本次完成条件，当前删除入口已硬停止。

Mock、HTTP 200、测试数量、构建成功或候选 revision 均不能替代登录、真实数据和
业务验收。
