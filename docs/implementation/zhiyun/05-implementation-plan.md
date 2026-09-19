# 智聘云开发蓝图：有序实施计划

## 1. 执行规则

本计划是编码顺序的权威入口。需求以 [01-requirements.md](01-requirements.md) 为准，边界以 [03-architecture.md](03-architecture.md) 为准，字段和状态以 [04-contracts-and-data.md](04-contracts-and-data.md) 为准。

全局规则：

- 每个任务先补测试，再改实现；只改任务列出的目录及同职责紧邻文件。
- 不清理无关代码，不提前实现后续任务，不改变稳定 ID 或公共错误码。
- Fake Provider 只允许测试和本地显式配置使用，不能伪装为生产成功结果。
- AI 结果必须来自真实已配置 Provider 并通过 Schema；失败必须显式呈现。
- 所有组织数据查询服务端强制 `org_id`；前端隐藏不是权限控制。
- 每个任务的必需检查失败时，记录命令和失败摘要，停止进入下一任务。
- 当前目录不是 Git 仓库；开始多人或发布开发前应初始化版本管理，但本计划不擅自创建远程仓库。

## 2. 依赖图

```text
TASK-001 migrations/config
  -> TASK-002 identity/RBAC/audit
  -> TASK-003 tasks/outbox/RabbitMQ
       -> TASK-004 resume parsers/OCR
       -> TASK-006 RAG lifecycle/versioning
       -> TASK-008 questionnaire
       -> TASK-009 scheduling/notification
       -> TASK-010 AI text interview

TASK-004 -> TASK-005 matching/LangGraph/evidence
TASK-006 -> TASK-007 hybrid retrieval/model generation
TASK-002..010 -> TASK-011 frontend
TASK-003..011 -> TASK-012 analytics/observability
TASK-001..012 -> TASK-013 production release
```

## 3. 技术基线

| 区域 | 选择 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PyMySQL |
| 测试 | pytest、pytest-asyncio/httpx；MySQL/RabbitMQ/Redis 集成环境用 Compose |
| 消息 | RabbitMQ 3.13，Python Adapter 采用支持 publisher confirm、ack 和 DLX 的库 |
| AI | LangGraph；OpenAI-compatible Chat Adapter；独立 Embedding/Rerank/OCR Ports |
| 前端 | React 18、TypeScript、Vite 5、React Router；API Client 集中管理 |
| 存储 | MySQL 8.4、Redis 7、本地持久化文件；向量使用可替换 Index Port |
| 部署 | Docker Buildx `linux/amd64`、Docker Compose v2、SSH/SCP/SFTP |

包版本必须锁定到项目 manifest/lockfile，并在升级任务中单独评估；不得在业务任务中顺手全量升级。

### 3.1 需求到任务追踪

任务内的验证命令是实现证据，[06-verification-release.md](06-verification-release.md) 的测试层次、验收矩阵和发布演练是系统证据。

| 需求 | 实现任务 | 主要验证证据 |
| --- | --- | --- |
| `FR-001` 至 `FR-002` | `TASK-005`,`TASK-011` | 岗位/规则版本 API、不可变约束、管理台 E2E |
| `FR-003` 至 `FR-006` | `TASK-003`,`TASK-004`,`TASK-011` | 导入幂等、格式/OCR fixture、异步任务集成与上传 E2E |
| `FR-007` 至 `FR-010` | `TASK-002`,`TASK-005`,`TASK-011` | Matching Schema、边界分流、证据、人工改判和审计测试 |
| `FR-011` 至 `FR-013` | `TASK-008`,`TASK-009`,`TASK-011` | 问卷版本、令牌、幂等提交和通知入口测试 |
| `FR-014` 至 `FR-015` | `TASK-003`,`TASK-009`,`TASK-011` | MySQL 并发预约和 Provider 幂等合同测试 |
| `FR-016` 至 `FR-020` | `TASK-003`,`TASK-005`,`TASK-009`,`TASK-010`,`TASK-011` | 面试状态机、SSE 恢复、接管竞争、禁问和报告引用 E2E |
| `FR-021` 至 `FR-024` | `TASK-003`,`TASK-006`,`TASK-011` | 知识摄取、发布切换、撤回、重建和版本一致性测试 |
| `FR-025` 至 `FR-029` | `TASK-002`,`TASK-006`,`TASK-007`,`TASK-011` | ACL 负向测试、混合检索、引用/拒答评估和 Prompt Injection 测试 |
| `FR-030` 至 `FR-031` | `TASK-002`,`TASK-011`,`TASK-012` | 登录/RBAC、资源授权、审计和日志脱敏测试 |
| `FR-032` 至 `FR-033` | `TASK-003`,`TASK-012` | Outbox、confirm/ack、幂等账本、重试、DLQ 和恢复测试 |
| `FR-034` | `TASK-011`,`TASK-012` | 授权事实查询、统计集成测试和管理台 E2E |
| `FR-035` | `TASK-013` | 摘要校验、Linux 发布、Smoke、应用回滚和数据恢复演练 |
| `NFR-001` | `TASK-002`,`TASK-013` | 权限负向测试、HTTPS/端口/秘密与容器安全检查 |
| `NFR-002` 至 `NFR-003` | `TASK-002`,`TASK-003`,`TASK-005`,`TASK-006`,`TASK-007`,`TASK-010`,`TASK-012` | PII 标记扫描以及请求、任务、模型、知识和人工决定关联测试 |
| `NFR-004` 至 `NFR-005` | `TASK-001`,`TASK-003`,`TASK-006`,`TASK-009`,`TASK-010` | 重启/重复投递/索引重建/并发和数据库事实一致性测试 |
| `NFR-006` 至 `NFR-007` | `TASK-003`,`TASK-004`,`TASK-005`,`TASK-006`,`TASK-007`,`TASK-010`,`TASK-012`,`TASK-013` | 异步响应、队列积压、负载与 2 GiB 资源峰值测试 |
| `NFR-008` | `TASK-001` 至 `TASK-013` | 模块边界检查、版本化契约、Alembic 升级和各任务质量门 |
| `NFR-009` | `TASK-011` | 键盘、焦点、标签、对比度、响应式和浏览器 E2E |
| `NFR-010` | `TASK-001`,`TASK-013` | 迁移失败处理、备份、上一版本回滚和数据恢复演练 |

## 4. `TASK-001` 数据库迁移与配置基线

**目标**：让 schema、配置和健康状态可控，移除生产运行时自动建表。

**前置**：当前测试基线通过。

**文件范围**：

```text
apps/api/alembic.ini
apps/api/alembic/
apps/api/app/main.py
apps/api/app/config.py
apps/api/app/infrastructure/db.py
apps/api/app/infrastructure/models.py
apps/api/pyproject.toml
apps/api/requirements.txt
apps/api/tests/test_config.py
apps/api/tests/test_migrations.py
.env.example
deploy/compose/compose.lite.yml
```

**实施**：

1. 引入 Alembic，生成与当前表一致的基线，再按 [04-contracts-and-data.md](04-contracts-and-data.md#7-数据所有权与表) 以展开式迁移新增基础字段/表。
2. 从 `main.py` 删除模块导入阶段和 lifespan 的 `create_all`；测试数据库通过 fixture 或迁移初始化。
3. 用 Pydantic Settings 或等价强类型设置定义环境变量，生产缺少关键配置时启动失败。
4. `/health/live` 只证明进程活着；`/health/ready` 检查数据库和必要配置，不调用收费 Provider。
5. 配置日志级别、上传路径、最大文件大小、运行模式；禁止把真实秘密写入 `.env.example`。
6. 在 `apps/api/pyproject.toml` 统一配置 Ruff、Mypy 和 Pytest 的检查范围、严格度、测试路径与异步模式，使后续任务和 CI 使用同一套质量基线。

**禁止**：在启动时执行 `create_all`；把生产密码设为默认值；一次迁移删除旧列。

**验证**：

```powershell
$env:PYTHONPATH = "apps/api"
python -m pytest apps/api/tests/test_config.py apps/api/tests/test_migrations.py -q
alembic -c apps/api/alembic.ini upgrade head
alembic -c apps/api/alembic.ini current
```

完成条件：空库可升级，现有基线数据副本可升级，API 不自动改表，`NFR-008` 可验证。失败即停止。

## 5. `TASK-002` 真实身份、RBAC、组织隔离与审计

**目标**：替换可伪造请求头，建立所有后续功能的安全边界。

**前置**：`TASK-001`。

**文件范围**：

```text
apps/api/app/domains/identity/
apps/api/app/domains/audit/
apps/api/app/api/auth.py
apps/api/app/api/v1/auth_routes.py
apps/api/app/api/middleware/
apps/api/app/infrastructure/models.py
apps/api/app/main.py
apps/api/tests/unit/identity/
apps/api/tests/integration/test_authorization.py
apps/api/tests/integration/test_audit.py
apps/api/alembic/versions/
```

**实施**：

1. 实现组织、用户、角色绑定、会话/令牌、撤销和登录限流。
2. `Principal` 只由服务端验证产生；生产检测到 header mock 模式即启动失败。
3. 为现有招聘表回填并强制 `org_id`；所有 Repository 方法要求 Principal/OrgScope。
4. 实现资源级策略：面试官只能访问分配记录，审计员只读。
5. 审计服务记录敏感动作，before/after 使用字段白名单和脱敏器。

**禁止**：仅在路由上判断角色、Repository 无组织过滤、审计完整简历或令牌、可逆保存密码。

**验收**：`AC-006`、`AC-018`，`FR-030`、`FR-031`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/identity apps/api/tests/integration/test_authorization.py apps/api/tests/integration/test_audit.py -q
```

完成条件：伪造角色头无效，跨组织与未分配访问均被拒绝且无数据泄露，敏感操作可审计。失败即停止。

## 6. `TASK-003` 任务、Outbox 与 RabbitMQ

**目标**：用可靠消息替代数据库轮询，建立重试、DLQ、幂等和 SSE 事件底座。

**前置**：`TASK-002`。

**文件范围**：

```text
apps/api/app/domains/tasks/
apps/api/app/infrastructure/messaging/
apps/api/app/workers/main.py
apps/api/app/api/v1/task_routes.py
apps/api/app/infrastructure/models.py
apps/api/app/config.py
apps/api/tests/unit/tasks/
apps/api/tests/integration/test_outbox.py
apps/api/tests/integration/test_rabbitmq_consumers.py
apps/api/tests/integration/test_task_sse.py
deploy/compose/compose.lite.yml
apps/api/alembic/versions/
```

**实施**：

1. 创建 `tasks`、`task_attempts`、`outbox_events`、`consumer_deliveries`、`idempotency_records`、`stream_events`。
2. 业务命令与 Outbox 同事务；Publisher 使用租约、publisher confirm 和批次上限。
3. 声明持久化 Exchange、业务队列、重试队列和 DLQ；消息遵循统一 envelope。
4. Consumer 通过 `(consumer_name,event_id)` 去重，处理结果与账本同事务。
5. 实现任务快照、持久化进度事件和 `Last-Event-ID` 补发。
6. Worker 优雅停机：停止拉取、完成/释放当前租约、关闭连接。

**禁止**：先提交业务再裸发消息；自动 ack 后处理；无限重试；异常后静默 `continue`；用 Redis 作为任务事实源。

**验收**：为 `AC-001`、`AC-007`、`AC-009`、`AC-017` 提供基础。

**验证**：

```powershell
docker compose -f deploy/compose/compose.lite.yml up -d mysql redis rabbitmq
python -m pytest apps/api/tests/unit/tasks apps/api/tests/integration/test_outbox.py apps/api/tests/integration/test_rabbitmq_consumers.py apps/api/tests/integration/test_task_sse.py -q
```

完成条件：模拟 confirm 丢失、重复投递、Consumer 崩溃和 SSE 重连均保持一致。失败即停止并保留容器日志。

## 7. `TASK-004` 简历解析、OCR 与结构化档案

**目标**：正确处理承诺格式，并以质量门保护后续匹配。

**前置**：`TASK-003`；`PD-003` 未确认时可实现真实接口和测试 Adapter，但生产 OCR 开关保持关闭。

**文件范围**：

```text
apps/api/app/domains/documents/
apps/api/app/infrastructure/files/
apps/api/app/infrastructure/ocr/
apps/api/app/workers/resume_worker.py
apps/api/app/contracts/resume.py
apps/api/app/api/v1/candidate_routes.py
apps/api/tests/fixtures/resumes/
apps/api/tests/unit/documents/
apps/api/tests/integration/test_resume_pipeline.py
apps/api/alembic/versions/
```

**实施**：

1. 校验扩展名、MIME、magic bytes、大小、压缩炸弹和安全文件名；存储键与原文件名分离。
2. TXT、DOCX、文本 PDF 使用专用 Parser；图片/扫描 PDF 使用 OCR Port。
3. 保存 `resume_version`、按页/节的 `resume_blocks`、结构化 `parsed_profile`、解析器版本和质量。
4. 低置信度或关键页失败进入 `NEEDS_REVIEW`；不创建正常匹配事件。
5. 去重范围改为组织和业务上下文，不向其他组织暴露文件存在性。

**禁止**：统一 UTF-8 强解码所有格式；把 OCR 文本当 100% 可信；从文件名拼接磁盘路径；补造邮箱、经历或学历。

**验收**：`AC-001`、`AC-002`、`AC-003`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/documents apps/api/tests/integration/test_resume_pipeline.py -q
```

完成条件：各格式、重复文件、恶意类型、OCR 失败和低质量路径均有固定 fixture 与断言。失败即停止。

## 8. `TASK-005` 岗位版本、Matching、LangGraph 与证据

**目标**：完成岗位与评分规则版本化，并实现三路分析、Supervisor、确定性评分和人工复核。

**前置**：`TASK-004`，`TASK-002`；生产运行前确认 Chat Provider 和公平性政策。

**文件范围**：

```text
apps/api/app/domains/recruitment/
apps/api/app/domains/matching/
apps/api/app/orchestration/matching_graph.py
apps/api/app/infrastructure/ai/
apps/api/app/contracts/recruitment.py
apps/api/app/contracts/matching.py
apps/api/app/workers/matching_consumer.py
apps/api/app/api/v1/job_routes.py
apps/api/app/api/v1/analysis_routes.py
apps/api/tests/unit/recruitment/
apps/api/tests/unit/matching/
apps/api/tests/integration/test_job_versions.py
apps/api/tests/integration/test_matching_pipeline.py
apps/api/tests/evaluation/matching/
apps/api/alembic/versions/
```

**实施**：

1. 版本化岗位、Rubric 和 Prompt；为现有权重/阈值建立可追踪基线。
2. LangGraph 并行执行 Basic、Skill、Experience；输出 Pydantic Schema 和 `resume_block_id`。
3. Supervisor 校验证据、冲突和缺失；确定性服务计算总分与 80/60/40 路由。
4. Provider 错误、Schema 错误、无效引用或低置信度统一进入 `NEEDS_REVIEW`。
5. 保存 `analysis_runs`、`agent_runs`、`model_runs`、`routing_decisions`。

**禁止**：模型直接输出最终录用/拒绝；Prompt 硬编码权重；保存无引用总分；缺配置时返回演示分数；把“稳定性”作为敏感或不可解释淘汰因素。

**验收**：`AC-004`、`AC-005`、`AC-006`、`AC-017`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/recruitment apps/api/tests/unit/matching apps/api/tests/integration/test_job_versions.py apps/api/tests/integration/test_matching_pipeline.py apps/api/tests/evaluation/matching -q
```

完成条件：岗位和规则版本不可变且停用语义正确；边界、Provider 失败、无效证据、低置信度和人工改判均通过，评估报告记录数据集版本。失败即停止。

## 9. `TASK-006` RAG 文档生命周期与版本一致性

**目标**：在现有 RAG 基线上完成新增版本、ACL、重建、发布、撤回和索引清单。

**前置**：`TASK-003`、`TASK-002`。

**文件范围**：

```text
apps/api/app/domains/knowledge/document_service.py
apps/api/app/domains/knowledge/version_service.py
apps/api/app/domains/knowledge/index_manifest_service.py
apps/api/app/workers/knowledge_worker.py
apps/api/app/api/v1/knowledge_routes.py
apps/api/app/contracts/knowledge.py
apps/api/app/infrastructure/models.py
apps/api/tests/unit/knowledge/
apps/api/tests/integration/test_knowledge_lifecycle.py
apps/api/alembic/versions/
```

**实施**：

1. 保留现有上传、解析、分块和显式发布语义，迁移为不可变多版本。
2. 将角色 JSON 迁移为 `knowledge_acl`；所有管理接口按组织过滤并审计。
3. 实现 Parent/Child Chunk 元数据、摄取步骤状态、Index Manifest 数量校验。
4. 发布事务原子更新 `active_version_id`，旧版本变 `SUPERSEDED`；撤回立即清空活动版本。
5. 重建使用新索引版本；失败不影响当前已发布版本。

**禁止**：原地覆盖已发布 Chunk；解析完成即自动发布；向量写了一部分就标记 `INDEXED`；删除旧版本后才验证新版本。

**验收**：`AC-011`、`AC-015`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/knowledge apps/api/tests/integration/test_knowledge_lifecycle.py -q
```

完成条件：草稿隔离、并发发布、旧版本排除、重建失败和撤回均通过。失败即停止。

## 10. `TASK-007` 混合检索、Rerank 与引用生成

**目标**：将关键词基线升级为完整可评估 RAG 查询链。

**前置**：`TASK-006`；确认或配置真实 Embedding、Rerank、Chat Provider。

**文件范围**：

```text
apps/api/app/domains/knowledge/retrieval_service.py
apps/api/app/domains/assistant/
apps/api/app/infrastructure/search/
apps/api/app/infrastructure/ai/
apps/api/app/contracts/assistant.py
apps/api/app/api/v1/assistant_routes.py
apps/api/tests/unit/assistant/
apps/api/tests/integration/test_rag_pipeline.py
apps/api/tests/security/test_rag_authorization.py
apps/api/tests/security/test_prompt_injection.py
apps/api/tests/evaluation/rag/
apps/api/alembic/versions/
```

**实施**：

1. 实现 Query Normalization、Dense、BM25、ACL/活动版本过滤、RRF、去重、Rerank、证据门和上下文组装。
2. 在内容送入 Rerank/Chat 前完成组织、角色和版本过滤。
3. 生成输出包含 `answer`、`citation_ids`、`reliable`、`conflict`；服务端验证引用。
4. Provider 不可用时可返回授权检索证据或拒答，但不能编造生成答案。
5. 保存 Retrieval Run 和脱敏 Model Run，建立可复现配置版本。

**禁止**：只在 UI 隐藏无权限引用；将全库文本发给模型；允许文档改变工具/系统规则；硬编码答案；未引用内容作制度结论。

**验收**：`AC-012`、`AC-013`、`AC-014`、`AC-016`、`AC-017`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/assistant apps/api/tests/integration/test_rag_pipeline.py apps/api/tests/security/test_rag_authorization.py apps/api/tests/security/test_prompt_injection.py apps/api/tests/evaluation/rag -q
```

完成条件：权限泄露为零、引用可定位、无证据拒答、Prompt Injection 无策略改变，评估集产生版本化报告。失败即停止。

## 11. `TASK-008` 问卷

**目标**：建立问卷版本、候选人邀请、提交和评分，不直接承担发送。

**前置**：`TASK-003`、`TASK-002`。

**文件范围**：

```text
apps/api/app/domains/questionnaires/
apps/api/app/api/v1/questionnaire_routes.py
apps/api/app/api/public/questionnaire_routes.py
apps/api/app/contracts/questionnaires.py
apps/api/tests/unit/questionnaires/
apps/api/tests/integration/test_questionnaire_flow.py
apps/api/alembic/versions/
```

**实施**：实现不可变版本、题型 Schema、评分规则、令牌哈希、过期/撤销、幂等提交和发送 Outbox。

**禁止**：数据库保存明文令牌；编辑已发送版本；控制器内直接发邮件；AI 自由评分没有规则或人工复核。

**验收**：`FR-011` 至 `FR-013`、`AC-007` 的通知入口。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/questionnaires apps/api/tests/integration/test_questionnaire_flow.py -q
```

完成条件：过期、撤销、重复提交、版本固定和权限测试通过。失败即停止。

## 12. `TASK-009` 排期与通知

**目标**：实现并发安全预约和幂等外部通知。

**前置**：`TASK-003`、`TASK-008`；生产外发前确认 `PD-001`、`PD-002`。

**文件范围**：

```text
apps/api/app/domains/scheduling/
apps/api/app/domains/notifications/
apps/api/app/infrastructure/email/
apps/api/app/infrastructure/calendar/
apps/api/app/workers/notification_consumer.py
apps/api/app/api/v1/schedule_routes.py
apps/api/app/api/public/schedule_routes.py
apps/api/tests/unit/scheduling/
apps/api/tests/integration/test_scheduling_concurrency.py
apps/api/tests/contract/test_notification_provider.py
apps/api/alembic/versions/
```

**实施**：

1. 数据库事务和唯一约束裁决时段，Redis 仅可优化竞争。
2. 通知模板版本化；业务模块只创建 `notification.requested`。
3. 发送账本记录 Provider ID；网络超时时先查询 Provider/账本，再决定重试。
4. 取消预约产生补偿通知；外部已发消息只标记和人工处理，不能声称撤销。

**禁止**：用 Redis 锁作为唯一保证；重试时生成新幂等键；测试 Fake Provider 在生产启用。

**验收**：`AC-007`、`AC-008`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/scheduling apps/api/tests/integration/test_scheduling_concurrency.py apps/api/tests/contract/test_notification_provider.py -q
```

完成条件：高并发同一时段只有一个成功，超时重试不重复外发。失败即停止。

## 13. `TASK-010` AI 文字面试

**目标**：实现可恢复、可接管、可审核的文字面试。

**前置**：`TASK-005`、`TASK-003`；生产启用前确认 `PD-005`、`PD-006`。

**文件范围**：

```text
apps/api/app/domains/interviews/
apps/api/app/orchestration/interview_graph.py
apps/api/app/api/v1/interview_routes.py
apps/api/app/api/public/interview_routes.py
apps/api/app/contracts/interviews.py
apps/api/app/workers/interview_consumer.py
apps/api/tests/unit/interviews/
apps/api/tests/integration/test_interview_flow.py
apps/api/tests/e2e/test_interview_reconnect.py
apps/api/tests/e2e/test_interview_takeover.py
apps/api/alembic/versions/
```

**实施**：实现短期令牌、消息幂等、持久化 SSE、LangGraph 状态、禁问规则、状态版本、人工接管竞争、报告草稿和人工审核。

**禁止**：先调用模型后保存用户消息；接管后继续提交 AI 回复；模型直接发邮件/预约；未审核报告直接成为最终决定。

**验收**：`AC-009`、`AC-010`、`AC-017`。

**验证**：

```powershell
python -m pytest apps/api/tests/unit/interviews apps/api/tests/integration/test_interview_flow.py apps/api/tests/e2e/test_interview_reconnect.py apps/api/tests/e2e/test_interview_takeover.py -q
```

完成条件：断线恢复不重发、接管竞争安全、敏感问题被阻断、报告引用消息。失败即停止。

## 14. `TASK-011` 前端功能路由与企业管理台

**目标**：把当前单文件静态导航升级为可用的授权工作台。

**前置**：`TASK-002` 至 `TASK-010` 的相关 API 稳定。

**文件范围**：

```text
apps/web/src/app/
apps/web/src/api/
apps/web/src/features/auth/
apps/web/src/features/dashboard/
apps/web/src/features/jobs/
apps/web/src/features/candidates/
apps/web/src/features/pipeline/
apps/web/src/features/questionnaires/
apps/web/src/features/scheduling/
apps/web/src/features/interviews/
apps/web/src/features/knowledge/
apps/web/src/features/assistant/
apps/web/src/features/audit/
apps/web/src/main.tsx
apps/web/src/styles/
apps/web/tests/
apps/web/package.json
apps/web/tsconfig.json
```

**实施**：

1. 迁移 TypeScript、集中 API Client、认证状态、React Router 和错误边界。
2. 按 feature 构建岗位、候选人、管道、问卷、排期、面试、知识、助手和审计页面。
3. 异步任务使用 SSE 重连与快照回退；危险操作确认并要求原因。
4. 实现空、加载、失败、无权限、人工复核和 Provider 未配置状态。
5. 键盘、焦点、标签、对比度和响应式布局达到 `NFR-009`。

**禁止**：前端拼完整 Prompt；通过隐藏按钮代替授权；用样例数字填充仪表盘；把所有功能继续堆在一个文件。

**验收**：所有 AC 的可见操作路径，重点 `AC-001`、`AC-006`、`AC-009`、`AC-012`、`AC-018`。

**验证**：

```powershell
cd apps/web
npm run typecheck
npm run test
npm run build
npm run test:e2e
```

完成条件：主要角色 E2E、空/错/断线状态和无障碍自动检查通过。失败即停止。

## 15. `TASK-012` 统计、日志、指标与运维视图

**目标**：让业务事实、错误和 AI 质量可观测，同时适应 `lite` 资源。

**前置**：`TASK-003` 至 `TASK-011`。

**文件范围**：

```text
apps/api/app/domains/analytics/
apps/api/app/infrastructure/observability/
apps/api/app/api/v1/dashboard_routes.py
apps/api/tests/integration/test_dashboard_facts.py
apps/api/tests/security/test_log_redaction.py
deploy/compose/compose.lite.yml
docs/operations/
```

**实施**：

1. 统一结构化日志字段：时间、级别、服务、trace、task/event、错误码；默认脱敏。
2. 提供轻量 `/metrics` 或日志指标，覆盖请求、队列深度、任务耗时/失败、Provider 错误、RAG 拒答率。
3. 仪表盘直接从授权事实查询或可重建快照读取；不手工维护不可验证计数。
4. 为磁盘、内存、容器重启、DLQ、备份失败定义运维检查和告警接入点。

**禁止**：在 2 GiB 服务器默认部署完整 Prometheus/Grafana/Loki；日志完整 Prompt、简历、邮箱或令牌。

**验收**：`FR-034`、`NFR-002`、`NFR-003`。

**验证**：

```powershell
python -m pytest apps/api/tests/integration/test_dashboard_facts.py apps/api/tests/security/test_log_redaction.py -q
docker compose -f deploy/compose/compose.lite.yml config
```

完成条件：统计可追溯到事实数据，敏感测试标记不出现在日志。失败即停止。

## 16. `TASK-013` 生产发布、备份与回滚

**目标**：完成本地构建、传输、Linux 导入、迁移、健康门和回滚。

**前置**：全部前置任务和 [06-verification-release.md](06-verification-release.md) 的 CI 门通过。

**文件范围**：

```text
apps/api/Dockerfile
apps/api/Dockerfile.worker
apps/web/Dockerfile
deploy/compose/compose.lite.yml
deploy/scripts/
scripts/build-release.ps1
scripts/deploy.sh
scripts/rollback.sh
.env.example
docs/operations/
docs/implementation/zhiyun/
```

**实施**：

1. 镜像使用不可变版本，非 root 用户、健康检查、最小运行文件和可扫描依赖。
2. Compose 增加资源限制、健康依赖、日志轮转、内部网络、只必要端口和持久卷。
3. 发布包包含镜像归档、Compose、部署脚本、版本清单、拆分蓝图和 `SHA256SUMS`；Alembic 配置与迁移脚本封装在 API 镜像中，不作为服务器端源码单独交付。
4. 修正 gzip 导入路径并在目标 Linux 验证；摘要失败立即停止。
5. 发布前备份 MySQL/文件/当前配置；迁移成功后启动；readiness 和 Smoke 失败自动恢复应用版本。
6. 保存发布日志、激活版本和恢复说明；至少保留当前与上一版本。

**禁止**：上传源码；服务器 `docker build`；使用可变 `latest`；健康失败 `|| true`；无备份执行破坏性迁移；将生产 `.env` 打入包。

**验收**：`AC-019`、`AC-020`、`FR-035`。

**验证**：完整命令和步骤见 [06-verification-release.md](06-verification-release.md)；必须在与目标一致的 Linux 环境完成一次发布和一次回滚演练。

完成条件：摘要篡改能阻止部署，新版本失败能恢复上一镜像和可用数据库，外部副作用清单被保留。失败不得宣布生产就绪。

## 17. 分阶段编码提示

给后续编码模型的每阶段提示使用以下固定结构：

```text
阶段：TASK-###
目标：仅实现 05-implementation-plan.md 中该任务。
前置：确认所有依赖任务检查通过。
权威文档：01-requirements.md、03-architecture.md、04-contracts-and-data.md。
允许修改：任务“文件范围”所列位置及同职责测试 fixture。
禁止：任务“禁止”项、无关重构、提前实现后续任务、伪造 AI 结果。
执行：先测试，再最小实现，再运行任务全部验证命令。
完成报告：列出改动文件、行为、迁移、检查命令与结果、剩余风险。
停止条件：任一必需检查失败，保存失败摘要并停止，不开始下一任务。
```

实施方不得把阶段提示当成第二份需求；发现文档冲突时应停止并修订蓝图。
