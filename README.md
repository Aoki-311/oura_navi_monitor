# OurA Navi Monitor

LCS RAG APP 专用的用户使用数据分析平台。主导航只保留：

- `全体サマリー`
- `ユーザー分析`
- `ユーザー管理`

本仓库现在采用一套未版本化的正式契约：没有第二套 dashboard、旧 API
fallback、旧 BigQuery 读取链或关键词分类器。

全体サマリー提供 `全体`、`DM`、`HCS`、`ブロック` 四个页签。
`cohort=all|dm|hcs|block` 与日期、地域共同作用于整页指标及导出。全体是启用的
DM/HCS/ブロック MR 合集，比例按合并后的分子、分母重算。本社和管理员不进入
全体统计；MisW本社也不进入用户地图或个人分析。用户管理中的 `チーム` 仅 HCS
可填写，允许留空，切换到其他部門时清空；三类 MR 都保留 MR 经历。

## 文档入口

- [最终产品与数据规范](docs/OURA_NAVI_MONITOR_FINAL_SPEC.md)
- [字段白话辞典](docs/MONITOR_FIELD_CATALOG.md)
- [实施、删除与切换清单](docs/IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md)
- [日常更新与名单发布流程](docs/MR_ROSTER_RELEASE.md)：以 `scripts/routine_release.py` 为入口
- [文档权威说明](docs/README.md)

## 当前本地代码

- `app/`：FastAPI、IAP 管理员校验、分析 API、用户名单/标签管理、
  Firestore 会话读取和增量任务。
- `frontend/`：三页面原生 ES Modules、Chart.js、日本 SVG 地图和响应式样式。
- `sql/`：同一 `oura_navi_monitor` dataset 内的正式事实表、唯一的
  `dashboard_events_v2` / `dashboard_user_list_v2` run-bound 读取合同和数据质量检查；不维护
  `user_daily` 或第二套 dashboard 快照。
- `scripts/`：默认仅输出 plan；需要云端写入的脚本必须同时提供精确参数、
  受批准凭据和 `--apply`。
- `deploy/`：唯一运行环境配置。Monitor Web 服务的候选创建由
  `cloudbuild.yaml` 负责；日常更新按 `scripts/routine_release.py` 的阶段与验收合同
  切换 reader。`scripts/promote_candidate.sh` 保留初次迁移用途及原有校验合同。

LCS 上游的结构化事件实现位于相邻仓库：

```text
../lcs_mrchatbot-main
```

修改两仓日志合同时，先用实际 YAML、运行环境和 LCS serializer 做一次无云端
依赖的联合检查：

```bash
.venv/bin/python scripts/verify_lcs_monitor_local_contract.py \
  --lcs-repo ../lcs_mrchatbot-main
```

这项检查只证明本地 producer/consumer 与部署配置相容，不替代 Cloud Build、
BigQuery Refresh Job、候选 API 或页面验收。

## 本地启动

生产模式读取 IAP 注入的 `x-goog-authenticated-user-email`，规范化邮箱后必须命中
三名管理员 allowlist；Cloud Run 继续禁止未认证访问。仅本机验收时，显式启用
本地管理员 header：

```bash
MONITOR_ALLOW_UNVERIFIED_LOCAL=true \
MONITOR_ADMIN_ALLOWLIST=2401145@tc.terumo.co.jp \
./scripts/run_local.sh
```

打开 `http://127.0.0.1:8080/dashboard`。本地 header 只用于明确启用的本机
测试；部署配置固定为 `MONITOR_ALLOW_UNVERIFIED_LOCAL=false`。

## 安全与发布边界

- 名单姓名、邮箱和标签只保存在 Monitor 专用 Firestore 集合；分析事件和
  BigQuery 事实表只使用 LCS 已验证登录 `user_id`，不保存邮箱或问答正文。
- 标签只影响 Monitor 展示，不能改变 MR/用户分析范围或 IAP 权限。
- 仓库修改不等于 BigQuery、Firestore、Logging、IAM、Scheduler、Cloud Run
  或流量已经改变。
- 历史原始来源 `run_googleapis_com_requests`、stdout、stderr 与 LCS Firestore
  不删除。旧 `monitor_answer_events` 只有在历史编译、数量核对和正式事实验收后
  才能删除；它的旧成功标志不会迁移成“完整交付率”。
- 本次核对的旧 DTS 仍启用、观察状态为 `FAILED`，仅写四张旧表。实际 BigQuery
  对象的依赖核对已证明它与当前 canonical API、增量 Chat/News 发布链分离。
  日常更新保留 DTS 配置，并重新验证精确查询和依赖证明；这不代表初次迁移完成。
  不调用仍读取旧 `monitor_answer_events` 的 `rebuild_history`；原始云端证据不入 Git。
- 构建成功、候选 revision、IAP 登录验收、业务验收和生产流量是六个不同
  状态，不能互相代替。

## 用户名单更新

`scripts/import_monitor_users.py` 按 A:G 标题识别唯一名单 sheet，不依赖 sheet 顺序。
当前来源是 `260811_OurANavi開発・運営業務チェックリスト_最終版_20261117.xlsx`
的 `OurA-Naviユーザー管理`；每次使用前重算最新文件哈希，其余开发、选项和地图 sheet
不会导入为用户。A:G 使用现有字段；H 列额外职务忽略，不保存、不显示；I 列
`HCSチーム` 仅为 HCS 保存到 `team`。G 列同时识别 `MR(ブロック)`、
`本社(MisW)`、`本社(セールスマーケ)` 及全角括号；旧 `MR`/`DM専任` 仍对应 DM。
本社/管理员的 `東京 + 虎ノ門` 在导入时
沿用已有 `本社・虎ノ門` 地区身份。MR 的所属区域以 A 列为准，不从勤務地推断。

默认仅核对工作簿，不联网。重复邮箱必须用 `--resolve-duplicate 'EMAIL=MR(HCS)'`
等明确选择其中一个部門，不能自动保留最后一行。2026-11-17 的名单输入为183人，
保留线上7人停用并启用新增13人时，预期启用176人、整体统计150人
（DM 64、HCS 75、ブロック11）、用户分析172人。这是核对基线，不是运行时常量；
最终使用 compare 的 `resultingRosterCounts` / `resultingScopeCounts` 和写后读回确认。

既有名单更新使用 `--sync`，按规范化邮箱匹配原记录，保留 roster ID、已绑定登录
身份、标签、启停状态和创建时间。已删除身份保留删除状态并跳过同步，旧Excel不能
将其恢复。未出现在工作簿中的用户保持原状。先读当前名单：

```bash
PYTHONPATH=. .venv/bin/python scripts/import_monitor_users.py '<LATEST_WORKBOOK_XLSX>' \
  --sync --compare-current \
  --credential-file '<APPROVED_CREDENTIAL_PATH>'
```

核对输出的新增数、修改数、字段变化数后，使用同一份输入及参数，移除
`--compare-current`，增加 `--apply --actor '<ADMIN_EMAIL>'`
和 `--expected-sync-digest '<syncDigest>'`。摘要变化时拒绝执行，必须重新比对。
同步逐用户事务提交并写审计，任一失败后重新比对可继续，不能把它当作全名单原子事务。
保留原无 `--sync` 的 bootstrap 模式，其禁止覆盖既有不同记录。

Monitor Web 支持读取 `summary_role_v1`、`summary_department_v2` 和新
`summary_department_v3` 已发布快照。旧策略按冻结的部门/范围规则验证，
不能用旧回执接纳新部门。v3 Refresh 发布前必须确保承接生产流量的Web能读取v3；
不能在旧Web仅支持v1/v2时提前发布v3快照。未知策略和损坏的快照仍拒绝读取。
新增名单需完成 Firestore 同步、Refresh Job 更新和新快照发布后才会出现在分析页面。
发布前应使用候选代码只读验证当前线上快照的全体、DM、HCS、ブロック、用户明细和 News 接口，
仅通过模拟 API 的浏览器测试或部署信息校验不足以确认数据可读。
`team` 只进入用户管理 Firestore，不增加 BigQuery 的个人资料字段。

地图使用 `DMエリア追加` sheet 的县域划分，并供四个页签共用。地图按エリア
统计，46 个可见都道府县归入 9 个区域；冲绳不绘制，但冲绳用户仍计入九州及所属
MR 人群。用户所属区域与县域图形是两个来源：例如最新名单中勤務地为新潟的
4 名 HCS 仍按 A 列计入北海道東北，新潟县图形按地图表归関東A。

Cloud Build 只创建无流量候选 Web 版本，不会自动同步名单或更新 Refresh Job。
构建后按 [日常更新流程](docs/MR_ROSTER_RELEASE.md) 保存 Chat/News 两条链路与
Scheduler 原始状态，冻结任务及租约；先切换兼容 v2/v3 的 reader，再仅更新 Job
镜像、按新生成的同步 digest 写入名单、刷新两条链路并完成 v3 真实验收，最后恢复
触发器原始状态并验证自然定时执行。本次保留原有 7 名停用用户，不删除真实用户，
不导入 H 列附加职务。新增关联用户的旧历史需单独核对覆盖，不能由增量刷新推定。

## 删除Monitor用户

用户管理区分有効、停用和删除。停用用户仍在默认名册中；删除确认后归档名册记录，
保留身份约束、历史日志及删除审计，默认列表隐藏该用户。删除不撤销OurA-Navi登录权限。
管理员 `DELETE /api/admin/users/{roster_id}` 必须携带当前 `expected_updated_at`
和 `expected_scope_policy_version`；并发变更拒绝覆盖，归档与审计在同一事务提交。
`include_deleted=true` 可读取删除记录，普通PATCH、身份绑定及Excel同步不能恢复它。
前端确认归档标记及默认列表均读回一致后显示成功；分析页在下一次成功发布快照后排除。
