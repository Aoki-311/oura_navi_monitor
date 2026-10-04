# OurA Navi Monitor

LCS RAG APP 专用的用户使用数据分析平台。主导航只保留：

- `全体サマリー`
- `ユーザー分析`
- `ユーザー管理`

本仓库现在采用一套未版本化的正式契约：没有第二套 dashboard、旧 API
fallback、旧 BigQuery 读取链或关键词分类器。

全体サマリー标题旁提供 `全体MR`、`MR(DM)`、`MR(HCS)` 三个筛选标签。
`cohort=all|dm|hcs` 与日期、地域共同作用于整页指标及导出。全体 MR 是有效的
DM/HCS MR 合集，不包含本社或管理员。用户管理中的 `チーム` 仅 HCS 可填写，
允许留空，切换到其他部門时清空；DM/HCS 都保留 MR 经历。

## 文档入口

- [最终产品与数据规范](docs/OURA_NAVI_MONITOR_FINAL_SPEC.md)
- [字段白话辞典](docs/MONITOR_FIELD_CATALOG.md)
- [实施、删除与切换清单](docs/IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md)
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
  `cloudbuild.yaml` 负责，正式流量只由 `scripts/promote_candidate.sh` 切换。

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
- 构建成功、候选 revision、IAP 登录验收、业务验收和生产流量是六个不同
  状态，不能互相代替。

## 用户名单更新

`scripts/import_monitor_users.py` 按 A:G 标题识别唯一名单 sheet，兼容旧工作表名和
新版 `Sheet1`，I 列 `HCSチーム` 保存到 `team`。G 列识别 `MR(DM)`、`MR(HCS)`
及全角括号；旧 `MR`/`DM専任` 仍对应 DM。新版本社/管理员的 `東京 + 虎ノ門`
在导入时沿用已有 `本社・虎ノ門` 地区身份，field 地区不推断。

默认仅核对工作簿，不联网。重复邮箱必须用 `--resolve-duplicate 'EMAIL=MR(HCS)'`
等明确选择其中一个部門，不能自动保留最后一行。2026-10-04 名单按用户确认排除
第 36 行、保留第 119 行 HCS 记录后，是 163 名用户，其中 MR 139 名
（DM 64、HCS 75），用户分析 160 名。人数是本次输入核对结果，不是运行时常量。

既有名单更新使用 `--sync`，按规范化邮箱匹配原记录，保留 roster ID、已绑定登录
身份、标签、启停状态和创建时间。未出现在工作簿中的用户保持原状。先读当前名单：

```bash
PYTHONPATH=. .venv/bin/python scripts/import_monitor_users.py ../userlist.xlsx \
  --resolve-duplicate 'EMAIL=MR(HCS)' --sync --compare-current \
  --credential-file '<APPROVED_CREDENTIAL_PATH>'
```

核对输出的新增数、修改数、字段变化数后，使用同一份输入及参数，移除
`--compare-current`，增加 `--apply --actor '<ADMIN_EMAIL>'`
和 `--expected-sync-digest '<syncDigest>'`。摘要变化时拒绝执行，必须重新比对。
同步逐用户事务提交并写审计，任一失败后重新比对可继续，不能把它当作全名单原子事务。
保留原无 `--sync` 的 bootstrap 模式，其禁止覆盖既有不同记录。

Monitor Web 支持读取 `summary_role_v1` 和 `summary_department_v2` 已发布快照。
读取旧快照时先按原策略验证范围标志和指纹，再从已验证的用户范围派生 DM/HCS 筛选；
Web 更新不要求 Refresh Job 同时完成更新，未知策略和损坏的快照仍拒绝读取。
新增名单需完成 Firestore 同步、Refresh Job 更新和新快照发布后才会出现在分析页面。
发布前应使用候选代码只读验证当前线上快照的全体、DM、HCS、用户明细和 News 接口，
仅通过模拟 API 的浏览器测试或部署信息校验不足以确认数据可读。
`team` 只进入用户管理 Firestore，不增加 BigQuery 的个人资料字段。
