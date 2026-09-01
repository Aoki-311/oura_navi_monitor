# OurA Navi Monitor 文档入口

## 权威分工

- [OURA_NAVI_MONITOR_FINAL_SPEC.md](OURA_NAVI_MONITOR_FINAL_SPEC.md)：产品口径、
  唯一 owner、页面、指标、数据链和完成标准的唯一权威。
- [MONITOR_FIELD_CATALOG.md](MONITOR_FIELD_CATALOG.md)：上述契约的逐字段白话解释；
  不另行定义指标。
- [IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md](IMPLEMENTATION_AND_CUTOVER_CHECKLIST.md)：
  文件删除、验证、云端 STOP 条件、切换顺序和当前发布状态；不另行定义产品。
- [THREE_HOUR_RECOVERY_RUNBOOK.md](THREE_HOUR_RECOVERY_RUNBOOK.md)：本次 LCS 故障、
  两天补数、小时级 Scheduler 切换和旧 DTS 暂停的唯一操作顺序。文件名保留历史名称。

其他 Markdown/XLSX 是历史调查、原始盘点或用户产物，只能作为证据线索，不能覆盖
上述最终规范。尤其是 2026-08-22/23 的审计和字段 Excel 可能包含有偏样本、旧
commit 信息或 PII 示例，不能直接上传或当成当前生产事实。

仓库本地实现、Git 提交、Git 推送、构建、候选、IAP 登录验收、业务验收、云端
数据变更和生产流量是彼此独立的状态。请始终查看实施清单的状态矩阵。
