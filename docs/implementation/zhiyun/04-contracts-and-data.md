# 智聘云开发蓝图：API、事件与数据契约

## 1. 契约状态与兼容原则

- `CURRENT` 指工作区当前已经存在的行为，证据见 [02-current-state.md](02-current-state.md)。
- `TARGET` 指任务完成后必须达到的稳定契约。
- `/api/v1` 内允许增加字段，但不得删除字段、改变类型或改变既有枚举含义；破坏性变化使用新 API 版本。
- 数据库表是模块内部契约，不直接暴露 SQLAlchemy 实体。
- 事件 `event_type` 和 `schema_version` 是兼容边界；消费者必须拒绝无法理解的主版本。
- 时间统一为 UTC ISO 8601，展示层转换时区；ID 使用 UUID 字符串。

## 2. HTTP 通用契约

### 2.1 成功响应

`CURRENT` 已有类似 envelope，但请求 ID 固定为 `local`。`TARGET`：

```json
{
  "request_id": "req_uuid",
  "trace_id": "trace_uuid",
  "data": {},
  "error": null
}
```

### 2.2 错误响应

```json
{
  "request_id": "req_uuid",
  "trace_id": "trace_uuid",
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不符合要求",
    "details": [{"field": "title", "reason": "required"}],
    "retryable": false
  }
}
```

公共错误码：

| HTTP | 错误码 | 含义 |
| --- | --- | --- |
| `400` | `INVALID_STATE`、`INVALID_FILE_SIGNATURE` | 请求语义或状态不允许 |
| `401` | `AUTH_REQUIRED`、`TOKEN_EXPIRED` | 未认证或候选人令牌失效 |
| `403` | `FORBIDDEN` | 角色或资源授权失败 |
| `404` | `RESOURCE_NOT_FOUND` | 当前组织/授权范围内不存在 |
| `409` | `CONFLICT`、`IDEMPOTENCY_CONFLICT`、`VERSION_CONFLICT` | 并发或幂等冲突 |
| `413` | `FILE_TOO_LARGE` | 超过配置限制 |
| `415` | `FILE_TYPE_UNSUPPORTED` | 类型或签名不支持 |
| `422` | `VALIDATION_ERROR`、`AI_OUTPUT_INVALID` | 字段或结构化输出无效 |
| `429` | `RATE_LIMITED` | 平台或 Provider 限流 |
| `503` | `PROVIDER_UNAVAILABLE`、`MODEL_NOT_CONFIGURED` | 外部能力不可用 |

制度问答的无证据是已处理业务结果，HTTP `200`，`data.reliable=false`、`data.error_code=NO_RELIABLE_EVIDENCE`，便于保留查询与引用审计。

### 2.3 分页与并发

列表使用游标分页：

```text
?limit=20&cursor=<opaque>&sort=-created_at
```

`limit` 默认 20，最大 100。响应 `data.items`、`data.next_cursor`。更新资源必须携带 `If-Match: "<version>"` 或请求体 `version`；冲突返回 `409 VERSION_CONFLICT`。

### 2.4 幂等

以下命令必须接受 `Idempotency-Key`：

- 简历导入、分析重跑、人工决定；
- 问卷发送、预约、取消预约；
- 面试消息、人工接管；
- 通知/日历副作用；
- 知识上传、新版本、发布、重建、撤回。

服务端以 `(org_id, actor_id, operation, idempotency_key)` 唯一。相同键和相同请求摘要返回原结果；相同键不同请求返回 `409 IDEMPOTENCY_CONFLICT`。记录保存期限由 `PD-004` 决定，未确认前不得主动缩短。

## 3. 身份和授权边界

### 3.1 `CURRENT`

`X-User-Role`、`X-Org-Id` 由客户端提供，开发环境默认 `ADMIN/default`。

### 3.2 `TARGET`

- 内部用户：`POST /api/v1/auth/login` 创建服务端会话或签名访问令牌；Cookie 必须 `HttpOnly`、`Secure`、`SameSite=Lax/Strict`，状态改变请求具备 CSRF 防护。
- `Principal` 由服务端认证结果生成：`user_id`、`org_id`、`roles`、`session_id`。
- 候选人：使用只绑定一个资源、一个操作、一个到期时间的随机令牌；数据库只存哈希。
- 所有查询先添加 `org_id`，再执行角色和资源授权；无权限资源统一返回 `404` 或 `403` 的策略需在安全评审中固定。
- `X-User-Role` 只允许在测试环境通过显式配置启用，生产启动时检测到该模式必须失败。

## 4. API 清单

### 4.1 当前保留并演进

| 方法与路径 | TARGET 请求 | TARGET 响应/状态 | 权限与幂等 |
| --- | --- | --- | --- |
| `POST /api/v1/jobs` | `title`、`description`、`requirements`、`rubric` | `201 job + active_version` | HR；必需幂等键 |
| `GET /api/v1/jobs` | 游标、状态过滤 | `200 items/next_cursor` | HR/ADMIN |
| `POST /api/v1/candidates/import` | multipart：`job_version_id`、一个或多个 `files` | `202 imports[{candidate_id,task_id}]` | HR；必需幂等键 |
| `GET /api/v1/tasks/{id}` | 无 | `200 task` | 资源授权 |
| `GET /api/v1/tasks/{id}/events` | `Last-Event-ID` 可选 | SSE | 资源授权 |
| `GET /api/v1/candidates` | 岗位、阶段、任务状态、游标 | `200 items/next_cursor` | HR；面试官受分配限制 |
| `GET /api/v1/dashboard` | 时间范围、岗位过滤 | `200 facts` | 授权统计 |
| `POST /api/v1/knowledge/documents` | multipart：标题、角色、文件 | `202 document/version/task` | ADMIN；幂等 |
| `GET /api/v1/knowledge/tasks/{id}` | 无 | `200 ingestion task` | ADMIN |
| `POST /api/v1/knowledge/documents/{id}/publish` | `version_id`、`version` | `200 active_version` | ADMIN；幂等、乐观锁 |
| `POST /api/v1/assistant/query` | `question`、可选 `session_id` | `200 answer/citations/reliable` | 已登录内部用户 |

### 4.2 招聘与人工决定

| 方法与路径 | 请求/响应 | 规则 |
| --- | --- | --- |
| `GET /api/v1/candidates/{id}` | 候选人、当前简历版本、分析摘要、流程时间线 | PII 按角色脱敏 |
| `POST /api/v1/candidates/{id}/analysis-runs` | `resume_version_id`、`job_version_id` -> `202 task_id/run_id` | HR；幂等 |
| `GET /api/v1/analysis-runs/{id}` | Agent 结果、总分、证据、置信度、规则版本 | 不返回 Provider 原始隐私载荷 |
| `POST /api/v1/candidates/{id}/decisions` | `decision`、`reason`、`expected_version` | HR；拒绝/录用必须人工身份 |
| `POST /api/v1/candidates/{id}/assignments` | `interviewer_id`、范围、有效期 | HR；资源级授权依据 |

### 4.3 问卷

| 方法与路径 | 请求/响应 | 规则 |
| --- | --- | --- |
| `POST /api/v1/questionnaires` | 标题、题目、评分规则 -> `201` | HR；创建不可变版本 |
| `POST /api/v1/questionnaires/{id}/send` | 候选人、版本、到期时间 -> `202 notification/task` | 幂等，不直接调用 Provider |
| `GET /public/questionnaires/{token}` | 最小题目和到期信息 | 令牌哈希、限流 |
| `POST /public/questionnaires/{token}/responses` | 答案、客户端提交 ID -> `201` | 一次性/版本化提交 |

### 4.4 排期与通知

| 方法与路径 | 请求/响应 | 规则 |
| --- | --- | --- |
| `POST /api/v1/schedule-slots` | 资源、开始/结束、时区 -> `201` | HR/面试官 |
| `GET /public/schedules/{token}/slots` | 可用时段 | 不暴露内部日历详情 |
| `POST /public/schedules/{token}/reservations` | `slot_id`、客户端请求 ID -> `201` 或 `409` | 数据库唯一约束最终裁决 |
| `POST /api/v1/reservations/{id}/cancel` | 原因、版本 -> `200` | 幂等，产生取消通知事件 |
| `GET /api/v1/notifications/{id}` | 发送状态、Provider 状态摘要 | 不返回密钥或完整 Provider 报文 |

### 4.5 AI 文字面试

| 方法与路径 | 请求/响应 | 规则 |
| --- | --- | --- |
| `POST /api/v1/interviews` | candidate、job/resume version、plan -> `201` | HR |
| `POST /api/v1/interviews/{id}/invite` | 到期时间 -> `202 notification` | HR；幂等 |
| `GET /public/interviews/{token}` | 最小会话状态和披露信息 | 同意策略由 `PD-005` 决定 |
| `POST /public/interviews/{token}/messages` | `client_message_id`、文本 -> `202 event/task` | 先持久化、幂等 |
| `GET /public/interviews/{token}/events` | SSE，支持 `Last-Event-ID` | 只读当前会话 |
| `POST /api/v1/interviews/{id}/takeover` | 原因、预期状态版本 -> `200` | 分配面试官/HR |
| `POST /api/v1/interviews/{id}/complete` | 原因、预期状态版本 -> `202 report_task` | HR/面试官 |
| `POST /api/v1/interview-reports/{id}/review` | 结论、修订、原因 -> `200 REVIEWED` | 面试官/HR |

### 4.6 知识版本管理

| 方法与路径 | 请求/响应 | 规则 |
| --- | --- | --- |
| `POST /api/v1/knowledge/documents/{id}/versions` | 文件、ACL、变更说明 -> `202` | ADMIN；不可覆盖旧版本 |
| `POST /api/v1/knowledge/versions/{id}/rebuild` | 解析/索引配置版本 -> `202` | 新建索引尝试 |
| `POST /api/v1/knowledge/versions/{id}/withdraw` | 原因、预期版本 -> `200` | 活动版本撤回后立即不可检索 |
| `DELETE /api/v1/knowledge/documents/{id}` | 原因、预期版本 -> `202 purge_task` | 先逻辑删除；物理删除服从保留策略 |
| `GET /api/v1/assistant/queries/{id}` | 问题、答案、引用、运行元数据 | 本人或审计角色 |

## 5. SSE 契约

### 5.1 事件格式

```text
id: 1842
event: task.progress
data: {"task_id":"...","status":"PROCESSING","progress":40,"occurred_at":"..."}
```

- `id` 是持久化事件序号，不使用任务 ID。
- 服务端每 15 至 30 秒发送注释心跳；具体值由配置决定。
- 客户端重连携带 `Last-Event-ID`；服务端只补发之后事件。
- 事件保留期限由 `PD-004` 决定。若游标过旧，返回 `409 EVENT_CURSOR_EXPIRED`，客户端重新获取资源快照。
- SSE 只传状态和脱敏摘要，不传完整简历、Prompt 或 Provider 原始输出。

事件类型：`task.progress`、`task.completed`、`task.failed`、`interview.message`、`interview.state_changed`、`assistant.token`、`assistant.completed`、`assistant.failed`。

## 6. 领域事件契约

统一 envelope：

```json
{
  "event_id": "uuid",
  "event_type": "resume.parse.requested",
  "schema_version": 1,
  "aggregate_type": "candidate",
  "aggregate_id": "uuid",
  "org_id": "uuid",
  "occurred_at": "2026-09-13T00:00:00Z",
  "trace_id": "uuid",
  "causation_id": "uuid",
  "payload": {}
}
```

事件清单：

| 事件 | 生产者 | 消费者 | 最小 payload |
| --- | --- | --- | --- |
| `resume.parse.requested` | Recruitment | Document Worker | `task_id,file_id,resume_version_id` |
| `resume.parsed` | Document | Matching Trigger、SSE | `task_id,resume_version_id,quality_status` |
| `analysis.requested` | Recruitment/Document | Matching Worker | `analysis_run_id,job_version_id,resume_version_id` |
| `analysis.completed` | Matching | Recruitment、SSE、Analytics | `run_id,score,confidence,route` |
| `questionnaire.invitation.requested` | Questionnaire | Notification Worker | `invitation_id,template_version_id` |
| `reservation.created` | Scheduling | Notification/Calendar Worker | `reservation_id,starts_at,ends_at` |
| `reservation.cancelled` | Scheduling | Notification/Calendar Worker | `reservation_id,reason_code` |
| `interview.report.requested` | Interview | Interview Worker | `session_id,report_id` |
| `interview.completed` | Interview | Recruitment、Analytics | `session_id,report_id` |
| `knowledge.ingestion.requested` | Knowledge | RAG Ingestion Worker | `task_id,version_id` |
| `knowledge.indexed` | RAG Ingestion | Knowledge、SSE | `version_id,index_manifest_id` |
| `knowledge.withdrawn` | Knowledge | Index Cleanup Worker | `version_id` |
| `notification.requested` | 业务模块 | Notification Worker | `notification_id,channel,template_version_id` |

事件不得包含完整文件、完整简历、完整制度文本、候选人令牌或 Provider 密钥。Consumer 通过 ID 在授权的服务端上下文读取必要数据。

## 7. 数据所有权与表

字段为 TARGET 最小集合；常规表均含 `created_at`、`updated_at`，需要乐观锁的表含 `row_version`。

### 7.1 Identity 与 Audit

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `organizations` | `id,name,status` | `name` 组织内展示，不作为授权键 |
| `users` | `id,org_id,email,password_hash,status` | `UNIQUE(org_id,email)`；email 规范化 |
| `roles` | `id,code` | `UNIQUE(code)` |
| `role_bindings` | `user_id,org_id,role_id` | `UNIQUE(user_id,org_id,role_id)` |
| `sessions` | `id,user_id,token_hash,expires_at,revoked_at` | `INDEX(token_hash,expires_at)` |
| `audit_logs` | `id,org_id,actor_id,action,resource_type,resource_id,before_json,after_json,reason,trace_id` | `INDEX(org_id,created_at)`；只追加 |

### 7.2 Recruitment 与 Document

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `jobs` | `id,org_id,title,status,active_version_id` | `INDEX(org_id,status,created_at)` |
| `job_versions` | `id,job_id,version_no,description,requirements_json` | `UNIQUE(job_id,version_no)` |
| `rubric_versions` | `id,job_version_id,version_no,dimensions_json,thresholds_json,confidence_threshold` | `UNIQUE(job_version_id,version_no)`；权重和为 1 |
| `candidates` | `id,org_id,job_id,display_name,status,row_version` | `INDEX(org_id,job_id,status)` |
| `candidate_files` | `id,org_id,candidate_id,sha256,storage_key,mime,size` | `UNIQUE(org_id,candidate_id,sha256)`；不全局暴露 |
| `resume_versions` | `id,candidate_id,file_id,version_no,status,parser_version,quality_json` | `UNIQUE(candidate_id,version_no)` |
| `resume_blocks` | `id,resume_version_id,ordinal,section,page_no,text,text_hash` | `UNIQUE(resume_version_id,ordinal)` |
| `parsed_profiles` | `id,resume_version_id,schema_version,profile_json` | `UNIQUE(resume_version_id)` |
| `analysis_runs` | `id,org_id,candidate_id,job_version_id,resume_version_id,rubric_version_id,status,total_score,confidence,route,prompt_version` | 输入版本不可变；`INDEX(candidate_id,created_at)` |
| `agent_runs` | `id,analysis_run_id,agent_type,status,score,confidence,evidence_json,reason_codes_json,model_run_id` | `UNIQUE(analysis_run_id,agent_type)` |
| `routing_decisions` | `id,candidate_id,analysis_run_id,source,from_status,to_status,reason,actor_id` | `INDEX(candidate_id,created_at)`；只追加 |

### 7.3 Tasks 与 Messaging

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `tasks` | `id,org_id,type,aggregate_type,aggregate_id,status,progress,current_attempt,next_retry_at,last_error_code` | `INDEX(status,next_retry_at)` |
| `task_attempts` | `id,task_id,attempt_no,status,started_at,finished_at,error_code,error_summary` | `UNIQUE(task_id,attempt_no)` |
| `outbox_events` | `id,event_type,schema_version,aggregate_id,payload_json,status,available_at,published_at,lease_until` | `INDEX(status,available_at)` |
| `consumer_deliveries` | `consumer_name,event_id,status,attempt_no,lease_until,completed_at` | `UNIQUE(consumer_name,event_id)` |
| `idempotency_records` | `org_id,actor_id,operation,key,request_hash,response_status,response_json` | `UNIQUE(org_id,actor_id,operation,key)` |
| `stream_events` | `sequence,org_id,stream_type,stream_id,event_type,data_json` | `AUTO_INCREMENT sequence`；`INDEX(stream_id,sequence)` |

### 7.4 Questionnaire、Scheduling、Notification

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `questionnaires` | `id,org_id,title,status,active_version_id` | `INDEX(org_id,status)` |
| `questionnaire_versions` | `id,questionnaire_id,version_no,questions_json,scoring_json` | `UNIQUE(questionnaire_id,version_no)` |
| `questionnaire_invitations` | `id,candidate_id,version_id,token_hash,status,expires_at` | `UNIQUE(token_hash)` |
| `questionnaire_responses` | `id,invitation_id,submission_id,answers_json,score_json,submitted_at` | `UNIQUE(invitation_id,submission_id)` |
| `schedule_slots` | `id,org_id,resource_type,resource_id,starts_at,ends_at,status` | `CHECK(ends_at>starts_at)`；区间冲突由事务处理 |
| `reservations` | `id,slot_id,candidate_id,status,provider_event_id,row_version` | 活动状态下 `UNIQUE(slot_id)` |
| `notifications` | `id,org_id,channel,template_version_id,recipient_ref,status,idempotency_key` | `UNIQUE(org_id,channel,idempotency_key)` |
| `notification_deliveries` | `id,notification_id,attempt_no,provider,provider_message_id,status` | `UNIQUE(notification_id,attempt_no)` |

### 7.5 Interview

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `interview_sessions` | `id,org_id,candidate_id,job_version_id,resume_version_id,status,state_version,plan_json,coverage_json,taken_over_by` | `INDEX(candidate_id,status)` |
| `interview_tokens` | `session_id,token_hash,expires_at,revoked_at` | `UNIQUE(token_hash)` |
| `interview_messages` | `id,session_id,sequence,actor_type,client_message_id,content,model_run_id` | `UNIQUE(session_id,sequence)`、`UNIQUE(session_id,client_message_id)` |
| `interview_reports` | `id,session_id,status,schema_version,report_json,reviewer_id,reviewed_at` | `UNIQUE(session_id)` |

### 7.6 Knowledge、RAG 与 Provider

| 表 | 关键字段 | 约束/索引 |
| --- | --- | --- |
| `knowledge_documents` | `id,org_id,title,status,active_version_id,row_version` | `INDEX(org_id,status)` |
| `knowledge_versions` | `id,document_id,version_no,status,file_id,content_sha256,parser_version,embedding_model_version` | `UNIQUE(document_id,version_no)`、`UNIQUE(document_id,content_sha256)` |
| `knowledge_acl` | `version_id,subject_type,subject_value` | `UNIQUE(version_id,subject_type,subject_value)` |
| `knowledge_chunks` | `id,version_id,parent_id,chunk_type,ordinal,section,page_no,content,content_hash,token_count` | `UNIQUE(version_id,chunk_type,ordinal)` |
| `knowledge_index_manifests` | `id,version_id,index_version,dense_count,sparse_count,status,committed_at` | 一个版本只有一个活动 manifest |
| `assistant_query_logs` | `id,org_id,actor_id,question_redacted,answer_redacted,reliable,error_code,retrieval_run_id,model_run_id` | `INDEX(org_id,created_at)` |
| `assistant_citations` | `id,query_id,chunk_id,rank,retrieval_score,rerank_score,excerpt` | `UNIQUE(query_id,chunk_id)` |
| `retrieval_runs` | `id,query_id,query_hash,filter_json,dense_ids_json,sparse_ids_json,merged_ids_json,reranked_ids_json,config_version` | 内容按隐私策略裁剪 |
| `prompt_versions` | `id,purpose,version_no,template_hash,status` | Prompt 正文加密/受限访问；`UNIQUE(purpose,version_no)` |
| `model_runs` | `id,org_id,purpose,provider,model,prompt_version,status,latency_ms,input_tokens,output_tokens,error_code,trace_id` | 按组织统计；不保存完整敏感输入输出 |
| `provider_configs` | `id,org_id,capability,provider,model,secret_ref,status` | 密钥只保存秘密引用 |

## 8. 关键状态机

### 8.1 Task

```text
PENDING -> PROCESSING -> SUCCEEDED
                     -> RETRY_WAIT -> PROCESSING
                     -> NEEDS_REVIEW
                     -> FAILED
                     -> CANCELLED
```

进程崩溃后，租约过期的 `PROCESSING` 可重领；超过最大尝试进入 `FAILED` 或 `NEEDS_REVIEW`，不得无限循环。

### 8.2 Candidate

```text
IMPORTED -> PARSING -> PARSED -> ANALYZING -> ROUTED
                   \-> NEEDS_REVIEW
ROUTED -> QUESTIONNAIRE -> INTERVIEW -> DECISION_PENDING
ROUTED -> TALENT_POOL
任意有效阶段 -> CLOSED / ARCHIVED
DECISION_PENDING -> HIRED / REJECTED
```

`HIRED`、`REJECTED` 只能由人工命令进入。AI 可建议 `CLOSED` 路径，但在 `PD-006` 未确认前统一进入人工复核。

### 8.3 Knowledge Version

```text
UPLOADED -> PARSING -> OCR_REQUIRED -> PARSING
                   -> INDEXING -> INDEXED -> PUBLISHED -> SUPERSEDED
                   -> FAILED
PUBLISHED -> WITHDRAWN
```

只有 `PUBLISHED` 且等于 `knowledge_documents.active_version_id` 的版本可检索。

### 8.4 Notification

```text
PENDING -> SENDING -> SENT
                  -> RETRY_WAIT -> SENDING
                  -> FAILED
PENDING/SENT -> CANCELLED（仅表示平台状态；已发送内容不可撤销）
```

## 9. 并发、重试和回放

### 9.1 预约

预约事务：

1. 以 `SELECT ... FOR UPDATE` 锁定时段；
2. 验证状态和时间；
3. 插入活动预约，依赖唯一约束作为最终防线；
4. 同事务写 `reservation.created` Outbox；
5. 唯一冲突映射为 `409 CONFLICT`。

Redis 锁可降低竞争，但不能替代数据库约束。

### 9.2 Outbox 和 Consumer

- Publisher 采用租约，Broker Confirm 前不标记成功。
- Consumer 先登记 `consumer_deliveries`，再做业务写入。
- 外部发送通过 `notifications.idempotency_key` 和 Provider 支持的幂等键去重。
- DLQ 重放创建新的尝试记录，但沿用原 `event_id`；已经成功的 Consumer 不再执行。
- 管理员重放必须填写原因并审计。

## 10. AI 运行完整性

调用链：

```text
API/Worker
 -> validation
 -> orchestration
 -> prompt builder(versioned)
 -> provider adapter
 -> structured parser
 -> evidence/citation validator
 -> domain result
```

约束：

- Prompt 由服务端 `prompt_versions` 管理，前端不拼接完整 Prompt。
- Provider 密钥通过环境变量或秘密文件引用，禁止入库明文、日志、API 和发布包。
- 结构化响应使用 Pydantic Schema；JSON 截断、字段缺失、分数越界、无效证据 ID 均为 `AI_OUTPUT_INVALID`。
- Matching 结果必须引用当前 `resume_version` 的 block；RAG 引用必须属于本次授权证据集合。
- 生产配置缺失返回 `MODEL_NOT_CONFIGURED`，不得返回演示文字、随机分数或样例报告。
- Provider 回退必须记录实际 Provider/模型；回退失败进入人工复核或拒答。
- 模型调用日志只保留可追踪元数据和经批准的脱敏摘要。

## 11. RAG ACL 与 Prompt Injection

权限执行顺序固定为：

```text
Principal -> org filter -> active version filter -> ACL filter
          -> candidate IDs -> content fetch -> rerank -> model input
```

严禁先取回全部文本、发送给外部模型后再过滤展示。系统 Prompt 明确声明文档内容是不可信数据；RAG 路径默认没有写工具。未来只读工具也必须通过服务端白名单和参数授权，文档文本不能选择工具或修改参数。

## 12. 迁移、回填与回滚

### 12.1 基线迁移

`TASK-001` 使用 Alembic 接管 schema：

1. 对现有表生成并人工核对基线迁移；
2. 移除生产启动 `create_all`；
3. 新表先以兼容方式加入；
4. 为现有招聘数据回填默认组织，仅允许显式配置一个 `DEFAULT_ORG_ID`；
5. 对现有岗位、简历和知识记录生成版本行；
6. 校验数量、外键、唯一约束后再切换读取路径。

### 12.2 展开/迁移/收缩

破坏性字段变化分三次发布：先增加兼容字段和双写，再回填并切读，最后在确认无旧版本运行后删除旧字段。当前单机仍须遵守此模式，因为应用回滚可能运行旧镜像。

### 12.3 向量索引回放

向量和稀疏索引由 MySQL 中的知识版本与 Chunk 重建。重建写新 `index_version`，完成一致性检查后原子激活。回滚应用时选择兼容 manifest；不能只恢复向量库而不恢复 MySQL。

### 12.4 数据库回滚

优先前向修复。若迁移不可逆或损坏数据，停止写流量并从发布前备份恢复 MySQL 和文件存储，同时恢复对应上一版本镜像与 Compose。邮件、日历和已经被外部接收的通知不能随数据库恢复撤销，必须通过审计和补偿流程处理。
