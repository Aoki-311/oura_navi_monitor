# OurA Navi Monitor 字段白话辞典

本辞典回答“这个字段对非技术人员到底意味着什么”。字段按业务用途分组，不按
日志文件分组。所有“已实现”均指 2026-08-30 当前本地候选工作树；线上尚未部署，不能
把它理解为生产已经有数据。

状态说明：

- `本地已实现`：生产代码和 Monitor 消费合同已写好，等待候选/生产验证；
- `条件可用`：只有来源提供该值时才有，缺失必须显示 `-`；
- `页面现用`：三页面已经直接使用；
- `分析备用`：已经保存，可用于后续模块，但当前首页不一定展示。

---

## 1. 用户与名单

这些字段回答“是谁在使用、属于哪里、应该进入哪个分母”。姓名和邮箱只在
Monitor 专用 Firestore 与受 IAP 保护的 API 中出现，不进入分析日志或普通事实表。

| 字段 | 白话意思 | 来源/状态 | 可以分析什么 |
| --- | --- | --- | --- |
| `roster_id` | Monitor 内部给这名员工的稳定编号；邮箱改变也不换人 | 名单，本地已实现 | 打开个人页、连接历史 |
| `name` | 员工姓名 | Excel/用户管理，页面现用 | 用户表和个人页显示 |
| `email` | 当前员工邮箱 | Excel/用户管理，页面现用 | 首次匹配登录身份；不进 BQ |
| `user_id` | LCS 验证登录后得到的稳定 subject；未登录过的名单用户暂时为空 | LCS 身份解析 + Firestore 投影，本地已实现 | 把日志归到正确员工，不把邮箱写进日志 |
| `chat_user_id` | 已验证的 LCS Firestore 用户根文档 ID | Firestore 投影，条件可用 | 找到该员工会话 |
| `area` | Excel 的 `エリア` | 名单，页面现用 | 地区比较、地图排名 |
| `area_key` | SVG 联动使用的内部地区键 | 后端由 area/workplace 生成 | 地图着色和点击，不展示给用户 |
| `workplace` | Excel 的 `勤務地` | 名单，页面现用 | 个人工作地点 |
| `role` | Excel 的角色 | 名单，页面现用 | 同角色比较、活性度堆叠图 |
| `department` | `DM専任`、`ヘルスケア本社`、`DM本社` 或 `管理者` | 名单，页面现用 | 决定用户分析/地图的非管理员资格；不能单独决定 Summary |
| `mr_experience` | MR 经历；本社人员显示 `-` | 名单，页面现用 | 个人画像和后续分组 |
| `label_ids` | 这名员工在 Monitor 使用的标签 | 用户管理，页面现用 | Chip 展示；不能改变 scope/权限 |
| `is_active` | 当前是否仍计入有效名单 | 用户管理，页面现用 | 分母、休眠用户、停用保留历史 |
| `updated_at` | 名单最后修改时间 | 用户管理 | 管理审计 |
| `updated_by` | 最后修改该名单的 Monitor 管理员 | IAP 身份 | 谁改了名单 |

名单范围机械派生：

- `global_scope_enabled`：是否为有效名单且角色精确为 `本社MR` 或 `コントラクトMR`，从而进入全体 Summary；
- `user_map_scope_enabled`：是否为有效的非管理员名单用户，从而进入用户分析/地图/详细；
- `is_admin`：部门是否为名单中的 `管理者`，只用于排除分析，不代表 IAP 权限。

这三项只存在 BigQuery 小型 `user_scope` 投影，不在前端提供编辑开关。Summary flag
由规范化角色、用户分析资格和 `is_active` 共同派生；部门只拥有用户分析/地图的结构性
资格。这样停用用户的既有事实仍可重建，但不会继续显示在当前分析名单里。分析标签永远
不能授予或移除这两个范围。

---

## 2. 一次用户问题

一行 `question_events` 代表“一名已认证用户提交了一次通过 API 预检的有效问题”。

| 字段 | 白话意思 | 状态 | 可以分析什么 |
| --- | --- | --- | --- |
| `question_ts` | 用户提交问题的准确时间 | 本地已实现 | 小时分布、趋势、最后利用 |
| `question_date` | 按日本时区归属的日期 | 本地已实现 | DAU、再访、日趋势 |
| `request_id` | 这次问题贯穿回答和保存的连接编号 | 本地已实现 | 问题→回答→写回完整链 |
| `trace_id` | 工程排错用追踪编号 | 本地已实现 | 定位一次异常请求 |
| `conversation_id` | 所属会话 | 本地已实现 | 会话旅程、追问 |
| `turn_id` | 所属轮次 | 条件可用 | 多轮顺序 |
| `message_id` | 客户端为该轮准备的消息编号 | 条件可用 | 连接 Firestore 消息 |
| `mode` | `internal` 或 `websearch` | 页面现用 | 社内/Web 利用占比 |
| `device_class` | `desktop`、`mobile` 或 `unknown` | 条件可用、页面现用 | PC/移动端分布 |
| `endpoint_class` | `ask` 或 `ask_stream` | 本地已实现 | 当前请求入口分布 |
| `valid_question` | 是否是非空、通过预检的问题 | 本地已实现 | 有效提问分母 |
| `attachment_count` | 这次问题带了几个附件 | 条件可用 | 附件功能使用度 |
| `primary_question_category` | 这次问题最主要想完成的事情 | 页面现用 | 问题类型分布 |
| `question_categories[]` | 同一问题还包含哪些次要意图 | 分析备用 | 多意图详细分析 |
| `classification_status` | 已分类还是无法判断 | 本地已实现 | 分类覆盖率，不把未知伪装成业务类 |
| `is_multi_intent` | 一次是否同时问了多件不同类型的事 | 分析备用 | 复杂问题比例 |
| `analytics_tasks[]` | 用户想做信息确认、比较、制作文面等哪些任务 | 页面现用（个人） | 用户任务画像 |
| `primary_product_name` | 这次问题最主要涉及的产品名 | 页面现用 | 产品 Top 10 |
| `primary_product_key` | 主产品名的不可逆标准键 | 本地已实现 | 稳定聚合产品 |
| `product_names[]` | 同一问题提到的所有产品 | 分析备用 | 多产品需求 |
| `product_keys[]` | 所有产品的标准键 | 本地已实现 | 产品连接与去重 |
| `product_candidate_count` | RequestSpec 识别到几个“可能是产品”的候选 | 本地已实现 | 产品识别覆盖度分母 |
| `product_resolved_count` | 其中几个能在受管产品身份中找到唯一标准产品 | 本地已实现 | 有多少候选能安全进入产品图 |
| `producer_revision` | 哪个 LCS revision 产生了分类 | 本地已实现 | 分类变更追踪 |
| `producer_git_sha` | 产生分类的代码 SHA | 本地已实现 | 版本差异追踪 |
| `record_origin` | 这行来自新事件、Firestore 历史还是旧审计历史 | 本地已实现 | 区分历史能力，不建立第二套页面 |
| `measurement_profile` | 这一行究竟能测到使用、分类还是完整交付 | 本地已实现 | 解释为什么某些旧指标是 `-` |

Monitor 不保存问题原文。问题类型由 RequestSpec Builder 在现有同一次模型调用中
产生，SQL、Firestore reader 和 JavaScript 不再读原文做关键词判断。`classification_status`
会把真正无法判断的 `unclassified` 与生产者违反合同的 `producer_invalid`
分开；后者会阻止错误批次发布，不能静默混入“判定不能”。

产品名也不是模型自由文本。模型在同一次 RequestSpec 调用中只提名与 entity 主语
一致的候选，现有受管产品身份解析器确认后才输出标准产品名和不可逆 key。未解析
候选只增加候选数，不保存候选文字；因此 Top 10 不会为了“看起来完整”而猜产品，
页面会就地说明有多少问题因产品尚未解析而未纳入图表。

---

## 3. 一次回答尝试

一行 `answer_events` 代表“一次回答最终结束了”。正常、部分、错误和取消都必须
有终态。

### 3.1 是否成功

| 字段 | 白话意思 | 状态 |
| --- | --- | --- |
| `terminal` | 用户输出最终是 `final`、`error` 还是 `cancelled` | 本地已实现 |
| `runtime_status` | 后端执行是完成、失败还是取消 | 本地已实现 |
| `message_persisted` | assistant 回答是否真的保存进 Firestore | 条件可用；客户端必须写回 |
| `assistant_error_present` | 保存的 assistant 消息是否带用户可见错误 | 条件可用 |
| `answer_ts` | 被保存的原回答时间 | 本地已实现；让延迟重试只更新对应日期分区 |
| `writer_error_code` | Writer 是否报告生成错误 | 条件可用 |
| `measurement_available` | 判断成功所需字段是否全部到齐 | BigQuery 派生，页面控制 `-` |
| `complete_delivery` | 是否满足完整交付全部条件 | BigQuery 唯一公式，页面现用 |
| `primary_failure_reason` | 未完整交付的第一主原因 | BigQuery 唯一优先级 |
| `failure_stage` | 如果执行失败，停在哪个阶段 | 条件可用、分析备用 |
| `failure_code` | 有界错误类型 | 条件可用、分析备用 |
| `persistence_error_code` | 回答保存失败的原因 | 条件可用、分析备用 |

### 3.2 需求交付

| 字段 | 白话意思 | 状态 |
| --- | --- | --- |
| `demand_total` | 这次问题一共要求后端完成几件事 | 本地已实现 |
| `delivered_demand_count` | 完整回答了几件 | 本地已实现；Web 需候选验证 |
| `partial_demand_count` | 只回答了一部分的有几件 | 本地已实现；Web 需候选验证 |
| `omitted_demand_count` | 完全漏掉的有几件 | 本地已实现；Web 需候选验证 |
| `system_fault_count` | 因系统问题无法判断/交付的有几件 | 本地已实现 |
| `supported_claim_count` | 最终保留的有依据陈述数量 | 社内模式条件可用 |
| `unsupported_claim_count` | 未获支持的陈述数量 | 社内模式条件可用 |
| `citation_count` | 最终答案带了多少条引用 | 本地已实现 |

Web Writer 的机器注释如果缺失、格式错误或 demand ID 不完整，上述需求计数会保持
缺失，回答成功率显示 `-`，不会把回答猜成成功。

### 3.3 时间与版本

| 字段 | 白话意思 | 状态 |
| --- | --- | --- |
| `total_latency_ms` | 从开始处理到形成回答终态的总毫秒数 | 页面现用 |
| `stage_latency_ms` | RequestSpec、检索、排序、生成、验证等各阶段耗时 | 条件可用、分析备用 |
| `retry_count` | 本次执行发生了几次受控重试 | 条件可用 |
| `revision_name` | 实际处理请求的 Cloud Run revision | 本地已实现 |
| `git_sha` | 实际代码 SHA | 本地已实现 |
| `build_id` | 构建编号 | 本地已实现 |

不再保存静态 `release_channel`/`traffic_tag`，因为候选 revision 被提升到生产后
静态环境值不会自动变化，会制造“100% 生产仍显示 candidate”的假数据。

---

## 4. 一项具体需求

一行 `demand_events` 代表“一次问题中的一件具体要求”。

| 字段 | 白话意思 | 可以分析什么 |
| --- | --- | --- |
| `demand_id` | 这件要求在该问题内的编号 | 连接交付结果 |
| `demand_order` | 它在用户问题中的顺序 | 找主需求/次需求 |
| `question_category` | 这件要求属于哪种问题类型 | 多意图拆分 |
| `analytics_task` | 这件要求想完成什么任务 | 任务画像 |
| `product_names[] / product_keys[]` | 这件要求涉及哪些产品 | 产品 × 任务 |
| `requirement` | required 还是 optional | 主类和完成判断 |
| `delivery_state` | delivered、partial、omitted | 知道具体漏了什么 |
| `evidence_state` | supported、conflict、indeterminate | 依据充分/冲突/不确定 |
| `system_fault` | 是否受检索/验证等系统问题影响 | 内容缺口与系统缺口分开 |
| `reason_codes[]` | 有界原因代码 | 失败原因排行 |

不保存 `user_wording` 或 `requested_fact_or_task` 原文，避免把问题内容复制进 BQ。

---

## 5. 用户后续动作

一行 `answer_action_events` 代表用户对答案做了一次后续动作。

| 字段 | 白话意思 | 可以分析什么 |
| --- | --- | --- |
| `action` | feedback、regenerate、enhance 或 correction | 用户是否返工 |
| `feedback` | good / bad | 满意/不满意趋势 |
| `target_message_id` | 动作针对哪条回答 | 连接原回答 |
| `request_mode` | 普通再生成还是强化模式 | 功能采用 |
| `client_origin` | 从哪个前端入口触发 | 交互入口效果 |
| `action_ts / action_date` | 动作发生时间 | 反馈趋势 |

这些动作单独分析，不能把一次当时不完整的回答改成完整，也不能用差评把一次
技术上完整的交付改成失败。

---

## 6. 会话与追问

一行 `conversation_events` 代表一个会话当前的分析摘要；消息正文只在用户详细页
按需读取。

| 字段 | 白话意思 | 可以分析什么 |
| --- | --- | --- |
| `first_active_at` | 会话第一次有消息的时间 | 会话开始 |
| `last_active_at` | 最后一条消息时间 | 最近会话 |
| `user_message_count` | 用户一共发了多少条消息 | 对话深度 |
| `assistant_message_count` | assistant 一共写回多少条 | 保存完整性 |
| `followup_count` | 第一问之后又追问了多少次 | 追问率 |
| `active_days` | 该会话跨多少个日期 | 长期会话 |
| `primary_mode` | 该会话主要使用社内还是 Web | 会话模式 |
| `status` | active、hidden 等会话状态 | 当前/隐藏会话 |

用户详细页会话列表还显示 `title`、更新时间、消息数；消息列表只显示角色、时间和
正文。它们来自 LCS Firestore，不复制进 BigQuery，也不进入全局分析日志。

---

## 7. 引用与资料

一行 `citation_events` 代表最终保存回答中的一条引用。

| 字段 | 白话意思 | 当前可用性 |
| --- | --- | --- |
| `source_type` | SharePoint、Web 等来源类型 | 条件可用 |
| `document_key` | 引用资料的内部编号 | 条件可用 |
| `display_title` | 页面可理解的资料标题 | 条件可用，不含正文 |
| `page_number` | 引用页码 | 条件可用 |
| `access_status` | 链接是否可打开/受限 | 条件可用 |
| `source_system` | 更具体的来源系统 | 本地字段已留，当前生产投影待来源补充 |
| `trust_tier` | 来源可信层级 | 本地字段已留，当前生产投影待来源补充 |
| `primary_product_key` | 这条资料主要支持哪个产品 | 本地字段已留，当前 Firestore 投影未填充 |

因此首版可以做引用数量、来源类型和资料标题分析；不能把空的 `source_system`、
`trust_tier` 或产品连接假装成已具备的“资料贡献率”。

---

## 8. HTTP 与产品可用性

一行 `http_request_events` 代表 Cloud Run 收到的一次 HTTP 请求。

| 字段 | 白话意思 | 用途 |
| --- | --- | --- |
| `endpoint_class` | ask stream、message write、conversation 或 other | 哪个功能出错 |
| `method` | GET、POST、PUT 等 | 请求类型 |
| `status` | HTTP 状态码 | 真实 4xx/5xx |
| `latency_ms` | Cloud Run 整体请求耗时 | 接口延迟 |
| `revision_name` | 实际处理请求的 revision | 版本定位 |

该表不保存 IP、完整 URL 或完整 User-Agent。HTTP 错误与回答未完整交付是两件事，
不能合成一个“错误率”。它们用于受限告警和诊断，不作为首页主导航。

---

## 9. 页面直接使用的派生数据

| 派生字段 | 白话意思 |
| --- | --- |
| `activeUsers` | 期间真正提问的人数 |
| `adoptionRate` | 活跃人数 ÷ 名单人数 |
| `returnRate` | 至少两个日期使用的人数 ÷ 活跃人数 |
| `questionsPerActiveUser` | 每位活跃用户平均提问 |
| `completeDeliveryRate` | 可测量回答中的完整交付比例，并同时显示已测量/全部数量 |
| `p95LatencyMs` | 95% 问题在该时间内形成终态 |
| `activeDays7` | 最近 7 日使用了几天 |
| `questionCount7` | 最近 7 日用户消息/问题数 |
| `activity` | high、middle、low、dormant |
| `dataThrough` | 最后一个完整发布批次处理到的内部边界；API 保留，前端不直接显示具体时间 |
| `usageTrend[].isPartial` | 该日只统计到 `dataThrough`，当天柱形不能当作完整日 |
| `analyticsQuality.sourcePipeline.publishedRunId` | 页面当前数据所属的最后一次成功发布 |
| `analyticsQuality.sourcePipeline.latestRunStatus` | 最新一次刷新实际是 running、succeeded 还是 failed |
| `analyticsQuality.sourcePipeline.latestRunErrorCode` | 最新失败的有界原因；失败时页面继续显示上次成功数据 |
| `quarantinedEventCount` | 本次来源中被逐事件隔离、没有进入事实的数量 |
| `deduplicatedDeliveryCount` | 同一来源事件重复到达但只保留一份事实的数量 |
| `axisUnmeasuredFindingCount` | 使用事实仍保留，但分类/任务/产品某一轴因语义不合法而不计测的数量 |
| `batchBlockingFailureCount` | 会阻止新事实与水位发布的质量问题数量 |
| `productResolution` | 产品候选数、解析成功数、未完全解析问题数和解析率；只解释产品图覆盖范围 |

当前 Monitor revision 只读
`dashboard_events_v2(start,end,published_run_id)` 和
`dashboard_user_list_v2(history_start,as_of,published_run_id)`：两个函数都以
`pipeline_state` 最后切换成功的 `published_run_id` 精确读取同一份 run-bound
`user_scope` 名单投影。名单投影先完整写入新 run，质量通过后才原子切换发布指针，
所以刷新中间态仍完整读取旧 snapshot，不会把实时 Firestore 名单和已发布分析事实混用。

旧两参数 `dashboard_events(start,end)` / `dashboard_user_list(history_start,as_of)`
已经退出正式合同，应用、验证器和部署前置条件均不得调用。历史日志与新日志都保留在同一
套正式事实表中，由 v2 函数按同一 `published_run_id` 读取。不存在 `user_daily`、
overview/detail mega view、实时 Firestore 名单或其他第二 owner；地区、回访、用户列表和
产品矩阵都来自同一 published run 的正式问题事实与名单投影。

历史 `record_origin` 只有 `firestore_history` 与 `legacy_audit_history`。旧审计表只
迁移身份、时间、精确旧分类等可证明字段；旧 `answer_success_flag`、raw payload、
问答正文和邮箱不会进入正式事实。缺少新完整交付字段时
`measurement_available=false`、`complete_delivery=null`，不是 0% 或 100%。

`pipeline_run_event_manifest` 不保存问题正文或邮箱，只保存 event ID 的不可逆哈希、事件
family、本次 run 和 `canonical/deduplicated/row_quarantined` 去向；
`pipeline_event_issues` 保存同一哈希的原因、首次/最后发现时间和解决状态。这样“被排除”
不再等于“静默丢失”，但页面仍不会暴露原始标识。

---

## 10. 用户管理与审计

标签字段：`label_id`、名称、固定色、有效状态、使用人数、创建/修改时间和修改人。

审计字段：`change_id`、动作、对象类型、对象 ID、修改时间、修改人和过期时间。
用户/标签、内部唯一性 claim 与审计在同一个 Firestore transaction 中提交。
claim 只防止并发邮箱、身份键或标签名冲突，不参与分析范围、IAP 权限或页面状态。

导出任务字段：job ID、类型、创建人、文件名、行数、状态、CSV 内容、创建时间和
1 小时过期时间。下载前再次检查创建者，其他管理员不能下载该 job。

---

## 11. 现在还不能诚实宣称已经有的数据

以下内容虽然本地合同已准备，仍需真实候选/生产验证：

- Web Writer 对机器注释的真实遵循率；
- 四种事件在实际 Cloud Logging 中是否全部成为 `jsonPayload`；
- 当前生产客户端每次 assistant 保存是否都携带同一个 `request_id`；
- 生产 Firestore 全量最早时间和 `updatedAt` 查询索引；
- `source_system`、`trust_tier`、引用产品连接的来源覆盖；
- 新问题类型和产品的真实分布；
- 生产 PII 扫描、字段覆盖率和真实成功率。

这些项目未验证前只能显示缺失或不可用，不能用 fixture、历史抽样或默认值代替。
