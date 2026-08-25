# OurA Navi Monitor 最终产品与数据规范

更新日：2026-08-26

## 0. 文档地位与当前结论

本文件是 Monitor 产品口径、数据责任和切换顺序的唯一规范。系统不建立 `v2`、
`v3`、`legacy`、`shadow`、`backup` 或兼容 dashboard。LCS 已公开的
`/v3/ask/stream` 是上游业务路由，不属于 Monitor 版本命名。

当前结论是 **尚未完成**：本地业务代码已通过最终验证，但没有执行 commit、push、
Cloud Build、部署、候选、IAP 登录验收、业务验收、BigQuery/Firestore/Logging
写入或流量切换。线上空页面不能被本地测试结果冒充为已修复。

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
| 历史编译逐会话读取 Firestore，8 分钟仍卡住 | 一个 root stream、一个 conversations collection-group stream、一个 messages collection-group stream |
| 坏会话没有时间时用当前时间补齐 | `ProjectionDataError` 明确排除并计数，绝不制造使用日期 |
| 旧成功率语义错误 | 旧 `answer_success_flag` 不迁移；完整交付只读可证明的新字段 |
| `user_daily`、snapshot 和 mega view 会互相漂移 | 事实表 + 一个 `dashboard_events(start,end)` + 一个 `dashboard_user_list(start,today)` |

## 3. 唯一责任模块

| 责任 | 唯一 owner | 禁止的冲突路径 |
| --- | --- | --- |
| 69/80 分析范围 | `app/domain/analysis_scopes.py` | SQL/前端按邮箱、标签或人数再排除 |
| 问题类型运行时合同 | LCS RequestSpec producer + `app/domain/question_categories.py` 校验 | Monitor 关键词分类、别名猜测 |
| 历史旧分类转换 | `migrate_legacy_question_category()` | 运行时接受旧枚举 |
| 历史 Firestore 读取 | `FirestoreChatReader` | 每个会话再查询一次 messages |
| 会话/引用投影 | `app/jobs/project_firestore.py` | 用当前时间、正文关键词或第二份地区表补值 |
| 一次性历史合并 | `app/jobs/rebuild_history.py` + `sql/merge_history.sql` | 长期双读旧表 |
| 增量发布与水位 | `app/jobs/refresh_analytics.py` | DTS 全量重建、第二个 scheduler |
| 页面事实语义 | BigQuery `dashboard_events` table function | overview/detail 各自复制公式 |
| 页面组装 | `app/services/analytics_service.py` | JavaScript 重算 KPI |
| 前端请求生命周期 | `frontend/api/client.js` + page controller | 页面私有 fetch、旧请求覆盖 |
| 名单/标签/审计 | `user_management.py` + `user_directory.py` transaction | 前端直写数据库、两套标签状态 |
| 会话正文 | `conversation_history.py` 分页读取 LCS Firestore | 把正文复制进 BigQuery |

## 4. 用户名单、地区与范围

首次导入权威是 `../OurA-Navi_userlist.xlsx`。2026-08-25 只读核对结果：83 行；
`MR=61`、`本社（ヘルスケア）=8`、`本社（DM）=11`、`システム管理者=3`。

| Excel 備考 | Monitor 部门 | 全局 KPI | 用户/地图/详细 | 用户管理 |
| --- | --- | ---: | ---: | ---: |
| MR | DM専任 | 是 | 是 | 是 |
| 本社（ヘルスケア） | ヘルスケア本社 | 是 | 是 | 是 |
| 本社（DM） | DM本社 | 否 | 是 | 是 |
| システム管理者 | 管理者 | 否 | 否 | 是 |

所以当前验收基线是 69 / 80 / 83，但代码只根据部门和 `is_active` 动态计算，不写死
人数。标签只用于 Monitor 展示，不能改变范围或 IAP 权限。

地区直接使用 Excel `エリア`。`首都圏A` 保持原样；只有本社且勤務地为虎ノ門时
单独显示 `本社・虎ノ門`。其他用户沿用其 `エリア` 与 `勤務地`，不创建地点字典。

## 5. 页面最终结构

### 5.1 全体サマリー

| 模块 | 范围 | 内容 | 呈现 |
| --- | ---: | --- | --- |
| 核心 KPI | 69 | 活跃人数、利用率、回访率、人均提问、回答成功率、P95 | KPI 卡 |
| 利用环境/模式 | 69 | 时段、设备、社内/Web | 柱形 + 圆环 |
| 利用推移/问题类型 | 69 | 每日活跃/提问、新问题类型 | 双轴趋势 + 横条 |
| 活性度分布 | 69 | 高、中、低、休眠；地区/角色比较 | 圆环 + 100% 堆叠 |
| 用户一览 | 80 | 姓名、邮箱、エリア、最后使用、7 日使用日、7 日消息、成功率、活性度 | 表格 |
| 日本使用地图 | 80 | 活跃、提问、利用率、回访率、地区排名 | SVG 热力图 + 横条 |
| 产品需求 | 69 | 产品 Top 10、产品 × 问题类型 | 横条 + 矩阵热力图 |

地区点击只形成一个可关闭的地区筛选；不增加复杂全局筛选栏。

### 5.2 ユーザー分析

1. 个人摘要：区域、地点、部门、MR 资历、最后使用、活跃天数、提问数、日均提问、
   完整交付率、同地区/同角色比较；
2. 个人趋势：提问柱形 + 完整交付率折线；
3. 用户需求画像：产品、任务、问题类型、模式、设备；
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
部分 demand、无 system/writer/assistant error，并确认消息写回，才算成功。历史字段不足
时 `complete_delivery=null`；页面可以显示可测量子集的比例，但必须同时显示覆盖数量，
绝不能把未测量当失败或成功。

活性度唯一口径：最近 3 日至少 3 次为高；否则最近 7 日 1–2 次为中；否则最近
14 日至少 1 次为低；其余休眠。

## 7. 问题类型

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

## 8. 唯一数据链与正式对象

```text
retained Cloud Run request/stdout/stderr + LCS Firestore + retired audit table
                         ↓ one-time history compiler
canonical BigQuery facts ← bounded incremental canonical events
                         ↓ dashboard_events / dashboard_user_list
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
`user_scope`、`pipeline_runs`、`pipeline_state`。

正式语义入口只有：

- `dashboard_events(p_start_date, p_end_date)`
- `dashboard_user_list(p_history_start, p_today)`

不存在 `user_daily`、`dashboard_overview`、`dashboard_user_detail` 或 snapshot owner。
`monitor_answer_events` 是一次性输入，不是页面 fallback；历史成功标志、raw payload、
问题正文和邮箱均不迁移。历史 apply 和精确验证成功前不得删除它。

## 9. 2026-08-26 只读历史计划证据

使用批准凭据运行 plan，未写云端：

- 83 个 chat roots、2,195 个会话、7,265 条消息；
- 旧 `monitor_answer_events` 4,176 行去重为 3,441 个唯一请求/trace；
- 合并后 3,331 次范围内提问、3,215 个回答、1,546 个有效会话、18,357 条引用；
- 提问来源：Firestore 2,923；只有旧审计能补回 408；
- 完整交付可测量 111 / 3,215，其余必须显示未测量；
- 37 个无消息空会话排除；375 个管理员/范围外旧事件排除；312 个不在当前名单的
  旧身份排除；6 名 80 人范围名单用户没有可绑定聊天历史；
- 总耗时 62.1 秒，`issueCount=0`，数据截止日为 2026-08-25。

这些是只读时间点结果，可能随 Firestore/日志变化。它证明过去数据仍可恢复，不
代表已经写入 canonical facts。线上 `question_events`、`answer_events` 和 pipeline
水位仍为空时，新 Monitor 页面仍会没有分析数据。

## 10. 正式 API

```text
GET /api/analytics/overview
GET /api/analytics/regions
GET /api/analytics/users
GET /api/analytics/users/{roster_id}

GET /api/trace/conversations
GET /api/trace/messages

GET /api/admin/metadata
GET/POST/PATCH /api/admin/users...
GET/POST/PATCH/DELETE /api/admin/labels...
POST/GET /api/export/jobs...
```

静态页面资源使用 `no-store`，避免 revision 切换后浏览器继续组合旧 HTML 与新 JS。
生产身份只接受 IAP 注入邮箱并命中三名管理员 allowlist；这次 Monitor 分析升级不
新增任何 IAP key、页面 secret 或测试专用权限。

## 11. 唯一切换顺序

不建立 backup、shadow 或长期 fallback：

1. 最后一次本地修改后完成全量回归、JS 合同、脚本/YAML/SQL 检查和 E2E；
2. 只读 inventory，固定实际旧对象、DTS、raw 最早日期和历史 plan 确认串；
3. 另行获得云端写入授权后，创建/原地扩充事实表和 source views；
4. 执行历史编译 apply，并按每个 expected event ID 验证全部落表；
5. 用同一个增量 owner 以最长 24 小时窗口追到冻结当前水位；
6. 构建 Monitor 无流量候选，完成 IAP 登录、三页、旧数据和局部失败验收；
7. 此后才测试/切换 LCS 新 revision，制造 internal/Web、成功/失败/写回链的真实新事件；
8. 刷新增窗口并确认新数据在同一页面连续出现、口径不跳变；
9. 只有历史与新链均验收后，停止旧 DTS并精确删除旧派生对象；raw 三表保留；
10. 分别显式切换已验收的 LCS/Monitor 流量。

任何一步失败都修复这一个 owner，不恢复旧 API、旧表页面读取或第二套状态。

## 12. 完成标准

只有以下全部具备真实证据才算完成：本地最终代码验证；旧路径关闭；历史 apply
数量与 event ID 验证；增量水位；Monitor 构建/候选；IAP 登录；三页旧数据业务验收；
LCS 新 revision 真实事件；新旧连续性验收；明确流量；旧派生 owner 删除。

Mock、HTTP 200、测试数量、构建成功或候选 revision 均不能替代登录、真实数据和
业务验收。
