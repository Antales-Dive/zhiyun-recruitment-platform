# 智聘云开发蓝图：验证、发布与回滚

## 1. 验证原则

- 自动化结果、发布日志、备份文件和评估报告才是完成证据。
- 单元测试通过不能替代 MySQL、RabbitMQ、Redis、Provider 合同或浏览器 E2E。
- AI 质量数字必须附数据集版本、配置版本、运行日期和原始结果；未验证数字不得写成项目成果。
- 权限泄露、跨组织访问、未授权内容进入模型、重复外部发送属于零容忍门。
- 当前单机不声明高可用或 SLA；容量目标在 `PD-008` 确认后补充。

## 2. 测试层次

| 层次 | 覆盖 |
| --- | --- |
| 单元 | 状态机、评分、阈值、ACL 构造、RRF、引用校验、脱敏、错误映射 |
| API 合同 | envelope、错误码、分页、幂等、版本冲突、SSE 格式 |
| 集成 | MySQL 事务/约束、Outbox、RabbitMQ confirm/ack/DLX、Redis、文件存储 |
| Provider 合同 | Chat、Embedding、Rerank、OCR、Email、Calendar 的超时、限流、无效响应 |
| E2E | 登录到招聘流程、候选人邀请、断线恢复、人工接管、知识发布与问答 |
| 安全 | 越权、跨组织、令牌重放、路径穿越、伪造文件、日志 PII、Prompt Injection |
| AI 评估 | 匹配证据与分流、RAG 召回/引用/忠实度/拒答、面试禁问和报告依据 |
| 负载 | 批量上传、队列积压、单 Worker 稳态、SSE 连接、2 GiB 内存峰值 |
| 故障 | Provider 超时、Broker 重启、Consumer 崩溃、重复投递、磁盘不足、迁移失败 |
| 发布 | 摘要篡改、镜像导入、迁移、健康门、Smoke、应用回滚、数据恢复 |

## 3. 当前已验证基线

此前已运行并通过：

```powershell
$env:PYTHONPATH = "apps/api"
python -m unittest discover -s apps/api/tests -p "test_*.py" -v
python -m compileall apps/api/app

Set-Location apps/web
npm run build

Set-Location ../..
docker compose -f deploy/compose/compose.lite.yml config
```

证据摘要：后端 11/11 用例通过、Python 编译通过、前端 Vite 构建通过、Compose 配置可解析。`VERIFIED` 这些测试只覆盖当前基础实现，不代表生产就绪。

## 4. 目标持续集成门

实现完成后的本地/CI 顺序：

```powershell
$env:PYTHONPATH = "apps/api"
python -m ruff check apps/api
python -m mypy apps/api/app
python -m pytest apps/api/tests/unit -q
python -m pytest apps/api/tests/contract -q
python -m pytest apps/api/tests/integration -q
python -m pytest apps/api/tests/security -q
python -m pytest apps/api/tests/evaluation -q

Set-Location apps/web
npm ci
npm run typecheck
npm run test
npm run build
npm run test:e2e

Set-Location ../..
docker compose -f deploy/compose/compose.lite.yml config
```

在 CI 中启动隔离的 MySQL、Redis 和 RabbitMQ，不连接生产 Provider。Provider 合同使用录制的脱敏协议样本或受控沙箱；至少在预发布环境用真实 Provider 做一次端到端验证。

合并/发布硬门：

- 所有 P0 验收自动化通过；
- Alembic 从空库升级和从上一发布数据库副本升级均通过；
- 权限负向测试和日志敏感标记扫描通过；
- 没有运行时演示答案或随机结果；
- 前端构建产物无类型错误，核心 E2E 通过；
- Compose 配置、镜像扫描和发布包摘要通过；
- RAG/Matching 评估报告已归档；未冻结指标只能标为基线，不能声称达标。

## 5. AI 与 RAG 评估

### 5.1 数据集结构

建议以 JSONL 保存，每条：

```json
{
  "case_id": "rag-001",
  "dataset_version": "2026-09-13-v1",
  "question": "试用期请假如何处理？",
  "org_id": "eval-org",
  "role": "HR",
  "expected_document_ids": ["doc-policy"],
  "forbidden_document_ids": ["doc-finance-secret"],
  "expected_facts": ["需要直属负责人审批"],
  "must_refuse": false,
  "expected_conflict": false,
  "tags": ["policy", "acl", "citation"]
}
```

匹配数据集另外保存：

```json
{
  "case_id": "match-001",
  "job_version_id": "fixture-job-v1",
  "resume_version_id": "fixture-resume-v1",
  "expected_evidence_blocks": ["block-3"],
  "allowed_route": ["QUESTIONNAIRE", "NEEDS_REVIEW"],
  "forbidden_reason_codes": ["SENSITIVE_ATTRIBUTE"],
  "reviewed_by": "fixture-reviewer"
}
```

测试数据必须合成或经过授权和去标识化，不能把真实候选人简历直接提交到仓库。

### 5.2 指标与硬门

RAG 记录：

- Dense/BM25 各阶段 Recall@K；
- RRF 后和 Rerank 后 Recall@K；
- citation precision、citation coverage；
- answer faithfulness、拒答准确率、冲突识别；
- ACL 违规召回数和未经授权模型输入数。

Matching 记录：

- Schema 成功率、证据有效率、人工复核率；
- 路由与标注一致性、边界稳定性；
- 不同简历格式的解析成功率；
- 敏感属性使用违规数。

`DECIDED` ACL 违规、未经授权模型输入、无效 citation、敏感属性自动决策必须为 0。其余数值阈值在建立首个可复现基线并由业务确认后冻结，文档不虚构目标。

### 5.3 Prompt Injection 集

至少包含：

- 文档要求忽略系统规则；
- 文档要求输出 API Key/系统 Prompt；
- 文档伪造更高角色；
- 文档要求调用发送、删除或修改工具；
- 用户要求绕过组织和版本过滤；
- 引用 ID 注入和上下文边界混淆。

预期：文本可以作为普通证据被引用，但不能改变授权、工具、系统策略或输出秘密。

## 6. 负载与故障验证

### 6.1 Lite 容量基线

在与目标相同的 2 vCPU/2 GiB Linux 环境测试：

- API 上传快速返回并将任务入队；
- 单 Worker 并发 1 顺序处理 OCR/Embedding/Matching；
- 队列积压时 API 和任务查询仍可用；
- 容器总内存、交换、磁盘增长和重启次数被记录；
- 3 Mbps 下发布包传输耗时被记录。

测试输入规模由 `PD-008` 确认。确认前只输出曲线和瓶颈，不输出“支持 N 并发”的承诺。

### 6.2 必做故障

| 故障 | 期望 |
| --- | --- |
| API 提交事务后 Publisher 崩溃 | Outbox 恢复后发布，不丢任务 |
| Publisher confirm 丢失 | 可能重复投递，但 Consumer 只产生一次业务效果 |
| Consumer 处理中崩溃 | 租约过期后接管，尝试记录完整 |
| RabbitMQ 重启 | 连接恢复，持久消息仍在 |
| Provider 超时/429 | 分类重试，超过上限转人工或拒答 |
| OCR 部分页失败 | 质量状态明确，不进入正常匹配 |
| SSE 断线 | 通过持久化序号补发 |
| 知识新索引失败 | 当前发布版本继续可用 |
| 磁盘接近阈值 | 阻止新上传/发布并告警，不继续写满磁盘 |
| 迁移失败 | 不启动不兼容应用，保留旧版本恢复路径 |

## 7. 发布包

### 7.1 目录

```text
release/<version>/
  images/
    api.tar.gz
    worker.tar.gz
    web.tar.gz
  compose.lite.yml
  deploy.sh
  rollback.sh
  .env.example
  release-manifest.json
  SHA256SUMS
  docs/
    implementation/zhiyun/*.md
```

Alembic 配置和迁移脚本包含在 API 镜像中。服务器收到的是镜像和部署/说明文件，不接收可用于构建应用的源码树。

`release-manifest.json` 至少记录版本、构建时间、目标平台、三个镜像仓库标签与 digest、数据库 revision、前一兼容版本和文档版本。构建时间不是业务版本；版本必须不可变。

### 7.2 本地构建

在本地 Docker 环境运行全部 CI 门后：

```powershell
$Version = "20260913.1"
.\scripts\build-release.ps1 -Version $Version
```

构建脚本必须：

1. 使用 `docker buildx build --platform linux/amd64 --load`；
2. 使用版本化标签，禁止只使用 `latest`；
3. `docker save` 后压缩归档；
4. 将 `SHA256SUMS` 中路径统一为相对 POSIX `/` 路径；
5. 摘要文件最后生成，且不把自身写入自身摘要；
6. 不复制真实 `.env`、密钥、上传文件、数据库或测试隐私数据。

本地验证归档：

```powershell
Get-FileHash -Algorithm SHA256 .\release\$Version\images\*.tar.gz
docker run --rm --platform linux/amd64 zhiyun-api:$Version python -m compileall app
```

### 7.3 传输

服务器目录：

```text
/opt/zhiyun/
  releases/<version>/
  shared/.env
  backups/<timestamp>/
  current -> releases/<active-version>
```

传输：

```powershell
scp -r ".\release\$Version" "deploy@server:/opt/zhiyun/releases/"
ssh deploy@server "cd /opt/zhiyun/releases/$Version && sha256sum -c SHA256SUMS"
```

3 Mbps 网络可使用支持断点续传的 SFTP/rsync 替代 SCP，但最终仍以服务器摘要校验为准。不得开放 Docker Remote API。

## 8. Linux 预检

部署脚本在改变运行状态前验证：

```bash
uname -m                         # 必须为 x86_64/amd64
docker version
docker compose version
df -h /opt/zhiyun
free -m
test -r /opt/zhiyun/shared/.env
sha256sum -c SHA256SUMS
```

还必须检查：

- `.env` 权限建议 `600`，所有必填变量存在但不打印值；
- 磁盘能同时容纳发布包、解压/导入镜像、当前和上一版本、备份；
- `80/443` 和 SSH 端口策略正确；MySQL、Redis、RabbitMQ 不映射公网；
- 当前任务和 DLQ 状态已记录，发布窗口允许中断 Worker；
- 备份目录可写；
- Compose 使用固定 `--project-name zhiyun`，保证数据卷名称稳定。

任何预检失败都停止，不加载或切换新版本。

## 9. 导入、备份、迁移与启动

### 9.1 镜像导入

为避免不同 Docker 版本对压缩输入行为差异，显式解压：

```bash
for archive in images/api.tar.gz images/worker.tar.gz images/web.tar.gz; do
  gzip -dc "$archive" | docker load
done
```

导入后按 `release-manifest.json` 校验镜像标签、架构和 digest。

### 9.2 发布前备份

```bash
BACKUP="/opt/zhiyun/backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$BACKUP"

docker compose --project-name zhiyun \
  --env-file /opt/zhiyun/shared/.env \
  -f /opt/zhiyun/current/compose.lite.yml \
  exec -T mysql sh -c \
  'exec mysqldump --single-transaction -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
  > "$BACKUP/mysql.sql"

docker compose --project-name zhiyun \
  --env-file /opt/zhiyun/shared/.env \
  -f /opt/zhiyun/current/compose.lite.yml \
  exec -T worker tar -C /data/uploads -czf - . \
  > "$BACKUP/uploads.tar.gz"

cp /opt/zhiyun/current/compose.lite.yml "$BACKUP/"
cp /opt/zhiyun/shared/.env "$BACKUP/env.snapshot"
chmod 600 "$BACKUP/env.snapshot"
sha256sum "$BACKUP"/* > "$BACKUP/SHA256SUMS"
```

首次部署没有 `current` 时，创建空备份记录并注明无旧数据。备份完成后必须执行 `sha256sum -c`；恢复演练前不能把“生成了文件”视为可恢复证据。

### 9.3 数据库迁移

迁移从新 API 镜像执行，但业务容器尚未切换：

```bash
docker compose --project-name zhiyun \
  --env-file /opt/zhiyun/shared/.env \
  -f /opt/zhiyun/releases/<version>/compose.lite.yml \
  run --rm api alembic -c /app/alembic.ini upgrade head
```

镜像必须包含 Alembic 和迁移。迁移失败立即停止，不能启动新 API；根据迁移性质采用前向修复或恢复备份。

### 9.4 启动与健康门

```bash
docker compose --project-name zhiyun \
  --env-file /opt/zhiyun/shared/.env \
  -f /opt/zhiyun/releases/<version>/compose.lite.yml \
  up -d

docker compose --project-name zhiyun \
  --env-file /opt/zhiyun/shared/.env \
  -f /opt/zhiyun/releases/<version>/compose.lite.yml \
  ps

curl --fail --retry 15 --retry-delay 2 http://127.0.0.1/health/ready
```

禁止 `|| true`。readiness 失败即进入应用回滚。

## 10. Smoke Test

使用专用测试组织和账号，且不向真实候选人发送通知：

1. 登录并验证角色；
2. 创建测试岗位；
3. 上传合成 TXT 简历，确认 `202`、任务通过 RabbitMQ 完成；
4. 检查匹配结果包含证据与规则版本，或在 Provider 未启用时明确进入人工复核；
5. 上传测试知识、确认发布前不可查询；
6. 发布后查询并验证引用；
7. 无证据问题返回拒答；
8. 未授权角色查询受限知识，模型运行记录不得含受限 Chunk；
9. 查询健康、队列和 DLQ；
10. 删除或归档测试数据，保留发布审计。

通知、日历和 AI 面试只有在对应 Provider 与政策已批准时才做生产 Smoke；否则检查功能开关关闭且错误说明正确。

## 11. 回滚

### 11.1 应用回滚

适用于 schema 向后兼容且数据未损坏：

1. 停止新 Worker 消费；
2. 使用上一版本 Compose 和镜像启动；
3. 执行上一版本 readiness 和核心只读 Smoke；
4. 恢复 `/opt/zhiyun/current` 指向上一版本；
5. 记录失败版本、任务状态和 DLQ，不删除新镜像。

### 11.2 数据恢复

只在迁移不可兼容或数据损坏时：

1. 停止 API/Worker 写入并保留现场日志；
2. 校验备份摘要；
3. 恢复对应 MySQL 与上传文件；
4. 启动与该备份 schema 匹配的上一版本镜像；
5. 重建可派生的缓存和向量索引；
6. 对恢复点之后的外部副作用进行人工对账和补偿。

不得仅恢复数据库却保留不匹配的文件或应用版本。

### 11.3 不可自动回滚的副作用

- 已发送邮件、短信、站内推送；
- 已创建或更新的外部日历事件；
- 候选人已经查看或提交的链接内容；
- 已被人工据此采取的招聘决定；
- 外部 Provider 已留存的请求日志。

这些必须依赖通知账本、审计和人工补偿，不能在回滚报告中写成“已撤销”。

## 12. 验收矩阵

| 验收 | 主要任务 | 自动化/证据 |
| --- | --- | --- |
| `AC-001` | `TASK-003`,`TASK-004`,`TASK-011` | 简历异步导入集成 + E2E |
| `AC-002` | `TASK-003`,`TASK-004` | 幂等与跨组织重复测试 |
| `AC-003` | `TASK-004` | OCR 故障/低置信 fixture |
| `AC-004` | `TASK-005` | Matching Schema、证据和评估集 |
| `AC-005` | `TASK-005` | 80/60/40 边界与规则版本测试 |
| `AC-006` | `TASK-002`,`TASK-005` | 人工改判审计集成测试 |
| `AC-007` | `TASK-003`,`TASK-009` | Provider 幂等合同与超时测试 |
| `AC-008` | `TASK-009` | MySQL 并发预约测试 |
| `AC-009` | `TASK-003`,`TASK-010`,`TASK-011` | SSE 断线恢复 E2E |
| `AC-010` | `TASK-010` | 人工接管竞争 E2E |
| `AC-011` | `TASK-006` | 草稿不可检索集成测试 |
| `AC-012` | `TASK-007`,`TASK-011` | 已发布知识引用评估/E2E |
| `AC-013` | `TASK-007` | 无证据/冲突拒答集 |
| `AC-014` | `TASK-002`,`TASK-007` | ACL 在模型输入前的负向测试 |
| `AC-015` | `TASK-006` | 并发发布和旧版本排除测试 |
| `AC-016` | `TASK-007` | Prompt Injection 安全集 |
| `AC-017` | `TASK-003`,`TASK-005`,`TASK-007`,`TASK-010` | Provider 故障矩阵 |
| `AC-018` | `TASK-002`,`TASK-011` | API 与浏览器越权测试 |
| `AC-019` | `TASK-013` | 摘要篡改阻断发布演练 |
| `AC-020` | `TASK-013` | 上一版本应用回滚和数据恢复演练 |

## 13. 运维交接清单

- 生产域名、HTTPS 证书、SSH 密钥和最小权限部署账号；
- `/opt/zhiyun/shared/.env` 的所有者、权限、轮换方式和秘密来源；
- 当前/上一版本、数据库 revision、镜像 digest、部署时间和操作者；
- MySQL、文件备份计划、保留策略和最近一次恢复演练证据；
- RabbitMQ 队列、重试、DLQ 和人工重放流程；
- 磁盘、内存、容器重启、任务失败、Provider 错误和证书到期告警；
- 邮件/日历 Provider 的回调、配额、退信与幂等规则；
- Chat/Embedding/Rerank/OCR Provider 的模型、限流、预算和故障联系人；
- 候选人数据保留、AI 告知、公平性和人工复核政策；
- 已知限制：单机无高可用、单 Worker、无本地模型、未确认的 SLA；
- 故障时停止入口、保全日志、回滚、恢复和外部副作用对账步骤。

只有清单有明确责任人、生产阻塞项关闭、目标服务器发布/回滚演练通过后，才能将状态从“可实施”提升为“生产就绪”。
