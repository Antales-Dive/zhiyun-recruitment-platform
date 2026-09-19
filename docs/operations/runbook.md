# 智聘云运维手册（lite）

## 健康检查
- `GET /health/live`：进程存活；`GET /health/ready`：数据库与配置就绪（不调用付费 Provider）。
- 容器健康：`docker compose --project-name zhiyun ps`。

## 磁盘与内存告警（2 GiB 约束，NFR-007）
- `df -h /opt/zhiyun`：磁盘低于 20% 时阻止新上传/发布并告警（上传路由返回 413/503）。
- `free -m`：容器总内存接近 2 GiB 时排查队列积压与并发上传；单 Worker 并发 1。

## 队列与任务
- 队列深度：`GET /api/v1/metrics` 的 `zhiyun_outbox_pending`；RabbitMQ 管理端口仅回环绑定。
- DLQ：`zhiyun.tasks.dlq` 积压时人工重放（重放沿用原 event_id，成功过的 Consumer 不再执行；重放必须填写原因并审计）。
- 任务重试：`tasks.status=RETRY_WAIT` 与 `task_attempts` 为诊断依据；数据库是任务最终事实源。

## 备份
- 发布前备份 MySQL（mysqldump --single-transaction）与 `/data/uploads`（tar czf），校验 SHA256SUMS。
- 未完成恢复演练前，备份文件不视为可恢复证据。

## 告警接入点
- 容器重启次数、DLQ 消息数、`model_runs.status=FAILED`、任务 FAILED、磁盘阈值、证书到期。

## 已知限制
- 单机无高可用、单 Worker、无本地大模型；SLA 未确认前不对外承诺容量数字。
