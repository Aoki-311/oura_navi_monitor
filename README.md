# OurA Navi Monitor

LCS RAG APP 专用的用户使用数据分析平台。主导航只保留：

- `全体サマリー`
- `ユーザー分析`
- `ユーザー管理`

本仓库现在采用一套未版本化的正式契约：没有第二套 dashboard、旧 API
fallback、旧 BigQuery 读取链或关键词分类器。

## 文档入口

- [最终产品与数据规范](docs/OURA_NAVI_MONITOR_FINAL_SPEC.md)
- [字段白话辞典](docs/MONITOR_FIELD_CATALOG.md)
- [实施、删除与切换清单](docs/IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md)
- [文档权威说明](docs/README.md)

## 当前本地代码

- `app/`：FastAPI、IAP 管理员校验、分析 API、用户名单/标签管理、
  Firestore 会话读取和增量任务。
- `frontend/`：三页面原生 ES Modules、Chart.js、日本 SVG 地图和响应式样式。
- `sql/`：同一 `oura_navi_monitor` dataset 内的正式事实表、一个有日期参数的
  `dashboard_events` 语义函数、一个用户列表函数和数据质量检查；不维护
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
- 标签只影响 Monitor 展示，不能改变 69/80 范围或 IAP 权限。
- 仓库修改不等于 BigQuery、Firestore、Logging、IAM、Scheduler、Cloud Run
  或流量已经改变。
- 历史原始来源 `run_googleapis_com_requests`、stdout、stderr 与 LCS Firestore
  不删除。旧 `monitor_answer_events` 只有在历史编译、数量核对和正式事实验收后
  才能删除；它的旧成功标志不会迁移成“完整交付率”。
- 构建成功、候选 revision、IAP 登录验收、业务验收和生产流量是六个不同
  状态，不能互相代替。
