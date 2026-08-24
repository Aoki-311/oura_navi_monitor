# OurA Navi Monitor 最终产品与数据规范

## 0. 文档地位与当前边界

本文件是 OurA Navi Monitor 产品口径、数据契约和责任边界的唯一权威。

最终系统只有：

- 一套 LCS 分析事件；
- 一个 `oura_navi_monitor` BigQuery dataset；
- 一套正式事实表与聚合；
- 一套未版本化 API；
- 一个 `/dashboard`；
- `全体サマリー`、`ユーザー分析`、`ユーザー管理` 三个页面。

禁止建立带 `v2`、`v3`、`next`、`legacy`、`shadow`、`backup` 后缀的第二套
Monitor 数据、API 或页面。LCS 已存在的公开 `/v3/ask/stream` 路由不属于本次
Monitor 命名范围，不改名、不改变客户端响应。

截至 2026-08-24，本地工作树已经包含本规范的实现，但未 commit、未 push、
未构建、未创建候选、未登录验收、未业务验收、未切流，也没有修改任何云端
BigQuery、Firestore、Logging、IAM、Scheduler 或 Cloud Run 数据。具体状态和
验证命令以 [实施与切换清单](IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md) 为准。

---

## 1. 最终目标

OurA Navi Monitor 是 LCS RAG APP 的用户数据分析平台，不是以工程告警为主的
运维监控台。它回答六个业务问题：

1. 谁在使用，是否形成持续使用？
2. 用户在什么时间、设备和模式下使用？
3. 用户在问什么产品、想完成什么任务？
4. 回答是否完整交付并成功保存？
5. 每个地区、角色和个人的使用差异是什么？
6. 哪些会话、追问、资料和反馈能解释上述结果？

数据错误、刷新失败和日志中断仍会被记录和告警，但不占据用户主导航，也不
显示一条用户无法理解的“数据可信度状态条”。受影响的指标显示 `-` 或
`データ取得不可`，绝不能伪装成 0。

---

## 2. 已解决的系统性根因

| 根因 | 最终处理 |
| --- | --- |
| Monitor 依赖已经停产的文本日志事件 | LCS active answer path 直接输出四种固定结构化分析事件 |
| 问题分类在 LCS、SQL、Firestore reader、JavaScript 各算一遍 | RequestSpec Builder 成为唯一分类 owner，其他层只保存、汇总、显示 |
| 未知问题被默认当成“ネタ探し” | 独立 `unclassified / 判定不能`，绝不猜测 |
| 错误率和 P95 使用没有状态/耗时的错误来源 | HTTP 指标只读 request log；回答耗时只读终态 answer event |
| 回答成功率把拒答、部分回答、写回失败混为成功 | 统一完整交付公式和单一失败优先级 |
| 部分测量样本被静默缩小分母 | 只要范围中存在未测量回答，完整交付率显示 `-` |
| 69/80/管理员排除逻辑散落 | `analysis_scopes.py` 只根据部门和有效状态计算 69/80；IAP 独立 |
| 名单邮箱与日志身份不稳定且 BigQuery 含 PII 风险 | 同一 HMAC secret 生成 `user_key`；姓名邮箱只在 Monitor 名单层补齐 |
| 15 分钟全量重建、视图无边界 | 2 小时重叠窗口、5 分钟终态等待、分区 MERGE、质量门和水位在同一事务发布 |
| 旧 snapshot、旧 API、宽容 adapter 长期并存 | 新 owner 接管后旧文件、路由、SQL 和 fallback 已从本地工作树删除 |
| 候选/生产静态标签长期错误 | 分析事件不再保存静态 candidate 标签；使用 revision、Git SHA、build ID |
| Firestore 投影静默截断 500 会话/消息 | 删除静默 limit，完整流式读取变更范围 |
| 产品名直接采用模型自由文本，可能误收用户内容 | 模型只提名候选；现有受管产品身份解析器是唯一准入 owner，未解析候选只计覆盖度、不写名称 |
| 请求日志存在但分析事件整族停产时无法察觉 | 每批把成功 ask HTTP 与 `question_received` 对账；一侧缺失即阻止发布并告警 |

---

## 3. 唯一责任模块

| 事实 | 唯一 owner | 其他层允许做什么 |
| --- | --- | --- |
| 问题类型、任务、产品 | LCS RequestSpec Builder | BigQuery 原样投影，前端翻译 label |
| 回答运行终态 | LCS TMCS service | BigQuery 关联和汇总 |
| assistant 消息是否保存 | LCS message-write route | BigQuery 与 answer 关联 |
| 评价、再生成、强化、修正 | LCS action emitter | Monitor 分析用户后续行为 |
| 69/80 范围 | `app/domain/analysis_scopes.py` | API 按 scope 查询；前端不再排除 |
| 名单、邮箱、部门、标签 | Monitor 专用 Firestore + `user_directory.py` | BigQuery 只接收无 PII 范围投影 |
| 完整交付公式 | BigQuery `merge_incremental.sql` | Python/前端只读取结果；合同测试锁定 SQL 公式和优先级 |
| 页面指标 | `analytics_service.py` + 三个正式 API view | JavaScript 只做可视化适配 |
| 会话与消息正文 | LCS Firestore | 用户详细页按需分页读取，不复制进 BQ |
| 云端刷新 | `app/jobs/refresh_analytics.py` | 一个 Cloud Run Job、一个 Scheduler |
| Monitor 候选构建 | `cloudbuild.yaml` | 不接收生产流量 |
| Monitor 正式切流 | `scripts/promote_candidate.sh` | 必须显式 revision 和 `--apply` |

`message_persisted` 同时携带原 assistant `answer_ts`。因此客户端延迟重试仍按原回答
日期做有界分区更新，不依赖“写回必须与回答处在同一个刷新窗口”的偶然时序，也不
需要每 15 分钟扫描 180 天答案。

任何新功能应扩展上述 owner，不得在 SQL、API 或前端建立第二套判断。

---

## 4. 用户名单与分析范围

初次导入来源：

```text
../OurA-Navi_userlist.xlsx
```

Excel `備考` 只在初次导入时拆成部门：

| Excel 備考 | Monitor 部门 | 全局指标 | 用户/地图/详细 | 用户管理 |
| --- | --- | ---: | ---: | ---: |
| `MR` | `DM専任` | 是 | 是 | 是 |
| `本社（ヘルスケア）` | `ヘルスケア本社` | 是 | 是 | 是 |
| `本社（DM）` | `DM本社` | 否 | 是 | 是 |
| `システム管理者` | `管理者` | 否 | 否 | 是 |

初次导入验收：

- 全局分析 69 人：61 名 `DM専任` + 8 名 `ヘルスケア本社`；
- 用户一览、地图、地区排名、用户详细 80 人：上述 69 人 + 11 名 `DM本社`；
- 用户管理 83 人：上述 80 人 + 3 名 `管理者`。

69/80/83 只用于初次导入验收，运行时代码不写死人数。新增、停用或改变部门后
自动重新计算。

地区规则只保留用户要求的简单结构：

- `首都圏A` 继续作为东京业务区域；
- 地区键直接使用名单中的标准化 `エリア`，不维护第二张地区代码映射；
- 只有 `本社 + 虎ノ門` 使用独立的 `本社・虎ノ門`；
- 其他人沿用 Excel `エリア` 与 `勤務地`；
- 不向用户展示额外地点字典。

三名 Monitor IAP 管理员仍由部署 allowlist 控制。名单中的 `管理者` 部门、
任何标签或用户管理操作都不能授予、撤销或模拟 IAP 权限。

---

## 5. 三个页面的最终结构

### 5.1 全体サマリー

| 模块 | 分析范围 | 内容 | 可视化 |
| --- | --- | --- | --- |
| 1 主要 KPI | 69 | 期间利用者数、利用率、再访率、1 人平均提问、回答成功率、P95 | 六张 KPI 卡 |
| 2 利用环境・モード | 69 | 时间段、设备、社内/Web 模式 | 柱形图 + 两个圆环图 |
| 3 利用推移・質問タイプ | 69 | 每日活跃/提问、问题类型 | 双轴趋势 + 横向条形图 |
| 4 活性度分布 | 69 | 高、中、低、休眠；地区/角色构成 | 圆环 + 100% 堆叠条形图 |
| 5 ユーザー一覧 | 80 | 姓名、邮箱、地区、最后使用、7 日利用日数、7 日消息、回答成功率、活性度 | 可横向滚动表格 |
| 6 日本利用マップ | 80 | 活跃、提问、利用率、再访率和地区排名 | 日本 SVG 热力图 + 横向排名 |
| 7 製品ニーズ | 69 | 产品 Top 10、产品 × 问题类型 | 横向条形图 + CSS Grid 热力矩阵 |

地图和排名点击只产生一个可关闭地区 Chip：

- 用户表和地图仍按 80 人范围过滤；
- 其余首页模块只计算该地区属于 69 人范围的用户；
- 不增加时间以外的复杂全局筛选栏。

### 5.2 ユーザー分析

1. 个人利用摘要：地区、地点、部门、MR 资历、最后利用、利用日数、提问数、
   1 日平均提问、回答成功率、同地区/同角色平均。
2. 个人利用趋势：问题柱形 + 完整交付率折线。
3. 用户需求趋势：产品、问题类型、任务、模式、设备。
4. 会话旅程：保留会话列表 + 消息列表双栏和懒加载，不增加技术字段。
5. 标签 Chip + `ユーザー管理で編集`，编辑只在用户管理页面发生。

### 5.3 ユーザー管理

两个子页面：`ユーザー管理`、`ラベル管理`。

用户可新增和修改：姓名、邮箱、エリア、勤務地、角色、部门、MR 资历、标签、
有效/停用。停用只把用户移出当前名单分母和页面，不删除已经形成的事实；
`user_scope` 把结构性 69/80 资格与当前 `is_active` 分开保存，因此重建也不会
丢失停用前历史。邮箱标准化后唯一；改变邮箱时关闭旧 `user_key` 有效期并建立
新映射，历史仍属于同一 `roster_id`。

标签可新增、改名、改固定色、停用、删除。正在使用的标签不可删除。名单/标签、
内部唯一性 claim 与审计记录使用同一个 Firestore transaction，避免并发重名、
“数据已改但没有审计”或相反。claim 只是 Monitor 内部一致性索引，不是第二套
名单、权限或标签状态。
审计和临时 CSV 导出都有 `expires_at`，云端切换时启用 Firestore TTL。

---

## 6. 指标口径

### 6.1 首页 KPI

| 指标 | 白话定义 |
| --- | --- |
| 期间利用者数 | 选定时间内至少提交过 1 个有效问题的人数；不是单日 DAU |
| 利用率 | 期间利用者数 ÷ 该范围当前有效名单人数 |
| 再访率 | 至少在 2 个不同日期使用的人数 ÷ 期间利用者数 |
| 1 人平均提问 | 有效问题数 ÷ 期间利用者数 |
| 回答成功率 | 完整交付问题数 ÷ 全部有效问题数；存在任何未测量问题时显示 `-` |
| P95 应答时间 | 95% 已形成终态的问题不超过的总耗时；存在缺失终态耗时时显示 `-` |

### 6.2 回答成功率就是完整交付率

必须全部满足：

```text
terminal = final
runtime_status = completed
demand_total > 0
partial_demand_count = 0
omitted_demand_count = 0
system_fault_count = 0
message_persisted = true
assistant_error_present = false
writer_error_code 为空
```

礼貌拒答、范围外问题、部分回答、遗漏需求、系统错误、取消、超时、回答写回失败
都不算成功。每个失败只保留一个主原因：

```text
stream_failed
not_final
not_persisted
assistant_error
writer_error
system_fault
demand_omitted
demand_partial
measurement_missing
```

评价、重新生成、强化和修正是独立的用户行为，不能反向篡改一次回答当时的交付
事实。

### 6.3 活性度

| 区分 | 定义 |
| --- | --- |
| 高アクティブ | 最近 3 日有效问题至少 3 次 |
| 中アクティブ | 非高，最近 7 日有效问题 1–2 次 |
| 低アクティブ | 非高/中，最近 14 日至少 1 次 |
| 休眠ユーザー | 最近 14 日 0 次 |

名单 LEFT JOIN 使用事实，因此没有使用记录的人也会进入休眠分母。

---

## 7. 问题类型、任务与产品

问题类型只表达“用户想完成什么”，产品是另一条轴。

| Key | 页面日文 |
| --- | --- |
| `product_information` | 製品情報・仕様 |
| `price_product_code` | 価格・製品コード |
| `comparison_fit_selection` | 比較・適合・選定 |
| `usage_procedure` | 使用方法・手順 |
| `troubleshooting_safety` | トラブル・安全対応 |
| `sales_proposal` | 営業活動・提案作成 |
| `institution_gpo_market` | 医療機関・GPO・市場情報 |
| `document_search` | 資料・文書を探す |
| `other_general` | その他・一般質問 |
| `unclassified` | 判定不能 |

任务轴：

| Key | 页面日文 |
| --- | --- |
| `fact_lookup` | 情報確認 |
| `explanation` | 説明依頼 |
| `comparison_selection` | 比較・選定 |
| `procedure_guidance` | 手順確認 |
| `troubleshooting` | 問題解決 |
| `content_creation` | 資料・文面作成 |
| `source_retrieval` | 資料検索 |
| `market_research` | 市場・施設調査 |
| `other` | その他 |
| `unclassified` | 判定不能 |

规则：

- 每个 RequestSpec demand 同时产出问题类型、任务和产品候选；
- 同一问题可有多个类型，首页按第一个 required demand 的主类型只计一次；
- 产品候选必须同时对应 RequestSpec 的 entity 主语，并由现有受管产品身份解析器
  解析成功后，才保存标准产品名与不可逆产品 key；自由文本永不直接进入日志；
- 每个问题保存产品候选数和解析成功数，未解析产品不猜名称，页面用就地说明展示
  “有候选未纳入产品图”，而不是制造全局技术状态条；
- 前端只翻译 label，不认识旧别名；
- Web 模式由同一次 grounded Writer call 附带机器注释说明逐需求交付，后端在
  用户看到和保存答案前剥离；缺失/格式错误就 `measurement_missing`，不猜成功。

---

## 8. 唯一数据链

```text
LCS accepted question / terminal answer / message write / user action
                         ↓ structured stdout JSON
Cloud Logging + Cloud Run request logs
                         ↓ one Logging sink
BigQuery oura_navi_monitor raw tables
                         ↓ partition MERGE + Firestore projection
canonical facts + daily aggregates + pipeline state
                         ↓ three canonical API views
FastAPI analytics/admin/conversation APIs
                         ↓
three dashboard pages
```

Firestore 继续作为会话消息和 Monitor 名单/标签权威。BigQuery 不保存问答正文、
姓名、邮箱、IP、完整 URL、完整 User-Agent 或 token。

### 8.1 LCS 分析事件

只有四类：

- `question_received`
- `answer_completed`
- `message_persisted`
- `answer_action`

共同 envelope：事件/请求/会话/轮次/消息 ID、HMAC `user_key`、模式、设备、
endpoint、revision、Git SHA、build ID、固定 `payload_json`。唯一 serializer 对字段、
长度、邮箱样式内容、HMAC 用户键和 ID 字符集统一 fail closed；事件异常不得影响答案，
但会产生专用失败日志，且成功 ask 请求与事件对账会阻止残缺批次发布。

### 8.2 BigQuery 正式对象

Cloud Logging 原始表：

- `run_googleapis_com_requests`
- `run_googleapis_com_stdout`

事实表：

- `http_request_events`
- `question_events`
- `answer_events`
- `answer_action_events`
- `demand_events`
- `citation_events`
- `conversation_events`
- `user_scope`

日聚合与任务状态：

- `user_daily`
- `pipeline_runs`
- `pipeline_state`

API 只读三个视图：

- `dashboard_overview`
- `dashboard_user_list`
- `dashboard_user_detail`

原始源视图只有 `monitor_event_source` 与 `http_request_source`。所有事实表和日
聚合按日期分区；请求必须带日期条件和 maximum bytes billed。

### 8.3 增量任务

唯一 Cloud Run Job：`oura-navi-monitor-refresh`。

唯一 Scheduler：`oura-navi-monitor-refresh-quarter-hour`，每 15 分钟：

1. 从上次成功水位回看 2 小时；
2. 结束时间落后当前 5 分钟，发布范围内不允许仍无终态的问题；
3. 更新名单身份投影；
4. 投影变化会话和引用；
5. 幂等 MERGE 事实；
6. 只重算用户列表实际使用的受影响日期 `user_daily` 投影；地区和产品由同一
   事实查询按当前选择期间计算，不保留第二套未使用聚合；
7. 执行关键合同检查和覆盖度观察，包括成功 ask HTTP 与问题事件对账；
8. Firestore 投影、事实 MERGE、日聚合、关键 `ASSERT`、run 状态和水位在同一个
   BigQuery 事务提交；任一步失败都不发布半批数据。

原始重复事件、未知枚举、producer invalid、无名单问题、事件单侧缺失和发布范围内
仍无终态都属于关键失败。终态缺少
消息写回是覆盖度问题：不冻结所有其他分析，但该回答成功率不能被计算。

---

## 9. 正式 API

```text
GET /api/analytics/overview
GET /api/analytics/regions
GET /api/analytics/users
GET /api/analytics/users/{roster_id}

GET   /api/admin/users
POST  /api/admin/users
PATCH /api/admin/users/{roster_id}

GET    /api/admin/labels
POST   /api/admin/labels
PATCH  /api/admin/labels/{label_id}
DELETE /api/admin/labels/{label_id}

GET  /api/trace/messages
POST /api/export/jobs
GET  /api/export/jobs/{job_id}
GET  /api/export/jobs/{job_id}/download
```

用户名单状态、标签和资料字段使用同一个用户 PATCH，不再有 `/status`、`/labels`
第二套写入。URL 只使用 `roster_id`，不使用邮箱。CSV 是 1 小时有效、创建者专属的
job，不提供旧 GET CSV alias。

所有页面和 API 必须通过 IAP 签名 JWT（精确 audience、issuer、email、subject）
和三名 allowlist 双重检查。未签名 email header 不构成身份，生产环境拒绝本地
header。

---

## 10. 前端风格与交互

保留现有深色、高信息密度、青蓝色数据平台风格，不迁移 React、不新增构建体系。

统一原则：

- 同一成功/注意/失败/未知语义使用同一颜色；
- 日文采用自然业务表达：`主要KPI`、`利用率`、`再訪率`、`個人利用`；
- 表格 sticky header，移动端只在表格内部横向滚动；
- PC 六列 KPI、iPad 三列、手机单列；
- 会话双栏在手机变成上下结构；
- 日本地图有 hover、键盘操作、地区联动和虎ノ門独立 marker；
- 标签颜色来自后端固定色集合；所有用户可编辑文字在插入 HTML 前转义；
- 每次页面刷新先销毁 Chart.js 实例；
- 单个 API 失败只影响所属模块，不清空其他模块。

---

## 11. 已关闭的旧 owner

本地最终代码已经删除：

- 旧 metrics/history routers；
- `bigquery_metrics.py`、`firestore_history.py`、旧 Google auth helper；
- 旧静态 ops dashboard；
- 旧 dashboard adapter、metric status adapter、旧六分类兼容；
- 旧全量重建 SQL 和刷新脚本；
- 服务账号 key 创建脚本；
- 旧手工 Cloud Run 部署脚本和 mutable `latest` service YAML；
- 七份与本规范冲突的旧设计/计划文档；
- LCS 旧 observability freeze 文档和 active runtime 的旧日志 emitters。

LCS 中仍用于离线质量评估的历史 fixture/工具不属于 Monitor 生产读取链；不能把
它们重新接回 Monitor，也不能因本次用户分析升级擅自删除独立评估能力。

精确文件和未来云端对象删除清单见实施清单。

---

## 12. 原地切换原则

用户已决定不保留旧 BigQuery 全量、shadow dataset、backup table 或长期双读。
但 Cloud Run 页面所见日志实际来自有限保留期的 Cloud Logging；删除 BigQuery
不会让 Logging 自动回填已过期历史。

因此切换必须在维护窗口按以下顺序一次完成：

1. 完成本地最终验证；
2. 只读确认实际生产 revision、镜像 digest、Logging 保留边界/exclusion、
   Firestore 最早时间、BigQuery 对象和 DTS 完整 ID；
3. 用户确认唯一 `ANALYTICS_START_AT`，此前无法取得的数据永久舍弃；
4. 导入 83 人名单并验收 69/80/83；
5. 停止旧 DTS，建立正式事实表并把同名 sink 收窄到 request log 与结构化事件；
6. 部署 LCS 候选并用真实 internal/Web 问题验证四事件连接，使保留的同名 raw
   stdout 表取得正式 `jsonPayload` schema；
7. 发布正式 source view，从仍可取得的 Logging 和 Firestore 原地重建正式事实；
8. 通过行数、去重、关联、PII、枚举、完整交付和 UI 验收；
9. 在同名 request/stdout raw 表内删除 `ANALYTICS_START_AT` 之前的旧行，并按精确
   清单删除其他旧 BQ view/table、DTS、log metric 和 policy；不删除/重建 raw 表；
10. 创建 Monitor 无流量候选，完成 IAP 登录和业务验收；
11. 显式把已验收 revision 切到 100%；
12. 确认真实生产流量后结束维护。

如果中途失败，只能保持维护页、修复唯一新链，并从仍在 Logging/Firestore 的
数据重新构建。不得恢复旧 API、旧 snapshot 或双读 fallback。

---

## 13. 完成标准

只有同时满足以下条件，整个升级才可称为完成：

- LCS 四事件在 internal/Web、正常/部分/错误、写回成功/失败路径均有最终合同；
- 问题分类只有 RequestSpec Builder 一个 producer；
- 69/80 只有 `analysis_scopes.py` 一个 owner，83 只是管理名单数量；
- 标签不改变 scope 或 IAP；
- BigQuery 旧正式对象按精确清单删除，生产只剩一套；
- 结构化事件、事实表和导出不存在姓名、邮箱、问答正文或 token；
- 最后一次代码修改后 RED、回归、合同、类型/编译、lint/build 和本地真实页面
  链路全部重跑；
- LCS 和 Monitor 候选均完成实际运行验证；
- IAP 登录、三页面、地图、会话、标签 CRUD 和真实数据业务口径由用户验收；
- 已验收 revision 获得生产流量且旧链不再被读取。

Mock、固定 fixture、测试数量、HTTP 200、构建成功或候选 revision 都不能替代
登录验收、真实数据口径和业务验收。
